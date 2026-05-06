"""Palantir Pi-side relay client.

Runs on the Raspberry Pi as a single small Python process.  Connects to
the laptop's `/relay/ws` over a WebSocket and:

  * Streams 16 kHz int16 PCM from the local microphone (Op.AUDIO_IN)
  * Streams JPEG-encoded camera frames at the configured FPS (Op.VIDEO_FRAME)
  * Reports privacy switch state changes via Op.GPIO_EVENT
  * Plays back audio frames the laptop sends (Op.AUDIO_OUT)
  * Drives the LED + relays in response to Op.LED / Op.RELAY

No ML libraries are loaded here — this whole file should fly on Pi 3B
with stock Python 3.13.  Only deps: numpy, opencv-python(-headless),
sounddevice, websockets, gpiozero (graceful fallback when not on a Pi).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import queue
import socket
import ssl
import sys
import threading
from typing import Optional

import structlog

from palantir.logging import setup_logging
from palantir.relay.protocol import DEFAULT_AUDIO_SR_HZ, Frame, Op

logger = structlog.get_logger()


# ---------- Hardware shims ----------------------------------------------------


class _MicCapture:
    """Push 16-bit mono PCM chunks into a queue for the WS sender."""

    def __init__(self, sample_rate: int, chunk_ms: int, device: Optional[int]):
        import numpy as np
        import sounddevice as sd

        self._np = np
        self._sd = sd
        self._sample_rate = sample_rate
        self._chunk_samples = int(sample_rate * chunk_ms / 1000)
        self._device = device
        self._stream: Optional[sd.InputStream] = None
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._running = False

    def start(self) -> None:
        if self._stream is not None:
            return

        def _cb(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                logger.warning("mic_status", status=str(status))
            samples = (indata[:, 0] * 32767).astype(self._np.int16)
            try:
                self._queue.put_nowait(samples.tobytes())
            except queue.Full:
                # Drop the oldest chunk to keep latency bounded.
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(samples.tobytes())
                except queue.Full:
                    pass

        self._stream = self._sd.InputStream(
            device=self._device,
            samplerate=self._sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self._chunk_samples,
            callback=_cb,
        )
        self._stream.start()
        self._running = True
        logger.info("mic_started", sample_rate=self._sample_rate)

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.debug("mic_stop_failed", exc_info=True)
            self._stream = None

    def drain(self) -> None:
        """Discard any chunks currently queued.  Called on privacy engage
        so audio captured before the switch was thrown can't leak out
        when the user disengages."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    async def chunks(self) -> "asyncio.AsyncIterator[bytes]":
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                chunk = await loop.run_in_executor(
                    None, lambda: self._queue.get(timeout=0.1)
                )
                yield chunk
            except queue.Empty:
                continue


class _CameraBase:
    """Common JPEG/threading machinery shared between CSI and USB sources."""

    def __init__(self, width: int, height: int, fps: int, jpeg_quality: int):
        import cv2

        self._cv2 = cv2
        self._width = width
        self._height = height
        self._fps = max(1, fps)
        self._quality = max(1, min(100, jpeg_quality))
        self._latest_jpeg: Optional[bytes] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def _encode_and_store(self, frame_bgr) -> None:
        ok, buf = self._cv2.imencode(
            ".jpg", frame_bgr, [self._cv2.IMWRITE_JPEG_QUALITY, self._quality]
        )
        if not ok:
            return
        with self._lock:
            self._latest_jpeg = buf.tobytes()

    def latest(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    def drain(self) -> None:
        """Forget the last captured frame.  Called on privacy engage so a
        frame captured before the switch was thrown isn't sent later."""
        with self._lock:
            self._latest_jpeg = None


class _USBCameraCapture(_CameraBase):
    """Read frames from a USB / V4L2 camera via cv2.VideoCapture."""

    def __init__(
        self,
        device: int,
        width: int,
        height: int,
        fps: int,
        jpeg_quality: int,
    ):
        super().__init__(width, height, fps, jpeg_quality)
        self._device = device
        self._cap = None  # cv2.VideoCapture | None

    def start(self) -> None:
        if self._cap is not None:
            return
        cap = self._cv2.VideoCapture(self._device)
        cap.set(self._cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(self._cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        cap.set(self._cv2.CAP_PROP_FPS, self._fps)
        if not cap.isOpened():
            raise RuntimeError(f"failed to open USB camera {self._device}")
        self._cap = cap
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            "camera_started",
            backend="usb",
            device=self._device,
            width=self._width,
            height=self._height,
            fps=self._fps,
        )

    def _loop(self) -> None:
        import time

        period = 1.0 / self._fps
        last = 0.0
        while self._running and self._cap is not None and self._cap.isOpened():
            ret, frame = self._cap.read()
            if not ret:
                logger.warning("camera_read_failed")
                time.sleep(0.05)
                continue
            self._encode_and_store(frame)
            now = time.monotonic()
            sleep = period - (now - last)
            if sleep > 0:
                time.sleep(sleep)
            last = time.monotonic()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                logger.debug("camera_release_failed", exc_info=True)
            self._cap = None


class _CSICameraCapture(_CameraBase):
    """Read frames from the Raspberry Pi CSI (ribbon-cable) camera via
    libcamera/picamera2.

    picamera2 is shipped with Pi OS (`apt install python3-picamera2`).
    For the venv to see it, install-pi-relay.sh creates the venv with
    `--system-site-packages`.  If picamera2 isn't importable we raise at
    start() time so the auto-detection in `_make_camera_capture` can
    fall back to USB.
    """

    def __init__(
        self,
        width: int,
        height: int,
        fps: int,
        jpeg_quality: int,
    ):
        super().__init__(width, height, fps, jpeg_quality)
        self._picam = None  # picamera2.Picamera2 | None

    def start(self) -> None:
        if self._picam is not None:
            return
        try:
            from picamera2 import Picamera2
        except Exception as e:  # pragma: no cover - hardware-only path
            raise RuntimeError(
                "picamera2 not available; install with "
                "'sudo apt install python3-picamera2' and create the "
                "venv with --system-site-packages"
            ) from e

        picam = Picamera2()
        config = picam.create_video_configuration(
            main={"size": (self._width, self._height), "format": "RGB888"},
            controls={"FrameRate": float(self._fps)},
        )
        picam.configure(config)
        picam.start()
        self._picam = picam
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            "camera_started",
            backend="csi",
            width=self._width,
            height=self._height,
            fps=self._fps,
        )

    def _loop(self) -> None:
        import time

        period = 1.0 / self._fps
        last = 0.0
        while self._running and self._picam is not None:
            try:
                rgb = self._picam.capture_array()
            except Exception:
                logger.warning("csi_camera_read_failed", exc_info=True)
                time.sleep(0.05)
                continue
            # picamera2 hands us RGB; cv2.imencode wants BGR for correct
            # colours when the laptop side decodes.
            bgr = self._cv2.cvtColor(rgb, self._cv2.COLOR_RGB2BGR)
            self._encode_and_store(bgr)
            now = time.monotonic()
            sleep = period - (now - last)
            if sleep > 0:
                time.sleep(sleep)
            last = time.monotonic()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._picam is not None:
            try:
                self._picam.stop()
                self._picam.close()
            except Exception:
                logger.debug("csi_camera_close_failed", exc_info=True)
            self._picam = None


def _make_camera_capture(
    camera_type: str,
    device: int,
    width: int,
    height: int,
    fps: int,
    jpeg_quality: int,
):
    """Factory: pick CSI / USB / auto-detect.

    `auto` tries CSI first (it's the typical Pi case) and falls back to
    USB when picamera2 isn't importable or fails to open the sensor.
    """
    want = camera_type.lower()
    if want == "csi":
        return _CSICameraCapture(width, height, fps, jpeg_quality)
    if want == "usb":
        return _USBCameraCapture(device, width, height, fps, jpeg_quality)

    # auto
    csi = _CSICameraCapture(width, height, fps, jpeg_quality)
    try:
        csi.start()
        return csi
    except Exception as e:
        logger.info("csi_camera_unavailable_falling_back", error=str(e))
        try:
            csi.stop()
        except Exception:
            pass
        usb = _USBCameraCapture(device, width, height, fps, jpeg_quality)
        usb.start()
        return usb


class _Speaker:
    """Plays int16 LE PCM chunks the laptop sends.

    Each `RelayAudioOutput.play()` on the laptop side produces one Redis
    message — and one WS frame — containing an 8-byte header + samples.
    We chain them through sounddevice.play() one at a time.  This is
    crude (no buffering, blocks on each utterance) but matches the
    laptop's blocking semantics.
    """

    _MAGIC = b"PCM\x01"

    def __init__(self):
        import numpy as np
        import sounddevice as sd

        self._np = np
        self._sd = sd

    def play(self, payload: bytes) -> None:
        if len(payload) < 8 or not payload.startswith(self._MAGIC):
            logger.debug("audio_out_bad_header", size=len(payload))
            return
        sr = int.from_bytes(payload[4:8], "little")
        if sr == 0:
            # Stop/clear marker: drop whatever's playing.
            try:
                self._sd.stop()
            except Exception:
                logger.debug("audio_out_stop_failed", exc_info=True)
            return
        samples = self._np.frombuffer(payload[8:], dtype=self._np.int16)
        if samples.size == 0:
            return
        try:
            audio_float = samples.astype(self._np.float32) / 32768.0
            self._sd.play(audio_float, samplerate=sr)
            self._sd.wait()
        except Exception:
            logger.exception("audio_out_play_failed")


# ---------- Optional GPIO ----------------------------------------------------


class _Gpio:
    """Privacy switch + LED + relays.  Falls back to a no-op shim when
    gpiozero isn't importable (running on a Mac/laptop for testing)."""

    PRIVACY_PIN = 17
    LED_R = 22
    LED_G = 27
    LED_B = 24

    def __init__(self, on_privacy_change):
        self._on_privacy_change = on_privacy_change
        self._available = False
        self._switch = None
        self._led = None
        self._relays: dict[int, object] = {}
        try:
            from gpiozero import RGBLED, Button

            self._switch = Button(self.PRIVACY_PIN, pull_up=True, bounce_time=0.1)
            self._led = RGBLED(self.LED_R, self.LED_G, self.LED_B)
            self._switch.when_pressed = lambda: on_privacy_change(True)
            self._switch.when_released = lambda: on_privacy_change(False)
            self._available = True
            logger.info("gpio_available")
        except Exception:
            logger.warning("gpio_unavailable_using_mock")

    @property
    def privacy_engaged(self) -> bool:
        if self._switch is None:
            return False
        try:
            return bool(self._switch.is_pressed)
        except Exception:
            return False

    def set_led(self, r: float, g: float, b: float) -> None:
        if self._led is None:
            return
        try:
            self._led.color = (r, g, b)
        except Exception:
            logger.debug("led_set_failed", exc_info=True)

    def set_relay(self, pin: int, state: bool) -> None:
        if not self._available:
            return
        relay = self._relays.get(pin)
        if relay is None:
            try:
                from gpiozero import OutputDevice

                relay = OutputDevice(pin)
                self._relays[pin] = relay
            except Exception:
                logger.debug("relay_open_failed", pin=pin, exc_info=True)
                return
        try:
            relay.on() if state else relay.off()
        except Exception:
            logger.debug("relay_toggle_failed", pin=pin, exc_info=True)


# ---------- The relay client -------------------------------------------------


class PiRelayClient:
    """Run loop: connect, ship sensors, receive output, reconnect on drop."""

    def __init__(
        self,
        url: str,
        token: Optional[str],
        *,
        camera_type: str,
        camera_device: int,
        camera_width: int,
        camera_height: int,
        camera_fps: int,
        jpeg_quality: int,
        mic_device: Optional[int],
        audio_chunk_ms: int,
        audio_sr: int,
        no_video: bool,
        no_audio_in: bool,
        no_audio_out: bool,
        no_gpio: bool,
        verify_tls: bool,
    ):
        self.url = url
        self.token = token
        self.camera_type = camera_type
        self.camera_device = camera_device
        self.camera_width = camera_width
        self.camera_height = camera_height
        self.camera_fps = camera_fps
        self.jpeg_quality = jpeg_quality
        self.mic_device = mic_device
        self.audio_chunk_ms = audio_chunk_ms
        self.audio_sr = audio_sr
        self.no_video = no_video
        self.no_audio_in = no_audio_in
        self.no_audio_out = no_audio_out
        self.no_gpio = no_gpio
        self.verify_tls = verify_tls

        self._stop = asyncio.Event()
        self._privacy_engaged = False
        self._mic: Optional[_MicCapture] = None
        self._cam = None  # _CSICameraCapture | _USBCameraCapture | None
        self._speaker: Optional[_Speaker] = None
        self._gpio: Optional[_Gpio] = None

    # ----- Hardware lifecycle ------------------------------------------------

    def _start_hw(self) -> None:
        if not self.no_audio_in:
            self._mic = _MicCapture(self.audio_sr, self.audio_chunk_ms, self.mic_device)
            self._mic.start()
        if not self.no_video:
            # _make_camera_capture handles auto-detection (CSI first, USB
            # fallback) and calls .start() in the auto path.  For explicit
            # csi/usb we still need to call start() ourselves.
            self._cam = _make_camera_capture(
                self.camera_type,
                self.camera_device,
                self.camera_width,
                self.camera_height,
                self.camera_fps,
                self.jpeg_quality,
            )
            if self.camera_type.lower() != "auto":
                self._cam.start()
        if not self.no_audio_out:
            self._speaker = _Speaker()
        if not self.no_gpio:
            self._gpio = _Gpio(self._on_privacy_change)

    def _stop_hw(self) -> None:
        if self._mic is not None:
            self._mic.stop()
            self._mic = None
        if self._cam is not None:
            self._cam.stop()
            self._cam = None
        # _Speaker has no persistent resources.

    def _on_privacy_change(self, engaged: bool) -> None:
        self._privacy_engaged = engaged
        # The pump tasks check `self._privacy_engaged` and skip every
        # chunk while it's True, so no audio/video bytes leave the Pi
        # during a privacy hold.  We deliberately do NOT stop the mic
        # or camera devices: doing so terminated `_MicCapture.chunks()`,
        # which exited `_pump_audio`, which tripped the
        # `asyncio.wait(FIRST_COMPLETED)` in `_session()`, which tore
        # down the WebSocket -- so a privacy toggle was reconnecting
        # the relay every time.
        #
        # While privacy is engaged we still drain the buffers so a
        # "before/during" chunk that was queued doesn't get sent later
        # when the user disengages.
        if engaged:
            if self._mic is not None:
                self._mic.drain()
            if self._cam is not None:
                self._cam.drain()

    # ----- Network loop -------------------------------------------------------

    async def run(self) -> None:
        import websockets
        from websockets.exceptions import ConnectionClosed

        self._start_hw()
        backoff = 1.0
        try:
            while not self._stop.is_set():
                ssl_ctx: Optional[ssl.SSLContext] = None
                if self.url.startswith("wss://"):
                    ssl_ctx = ssl.create_default_context()
                    if not self.verify_tls:
                        ssl_ctx.check_hostname = False
                        ssl_ctx.verify_mode = ssl.CERT_NONE

                connect_url = self._auth_url()
                logger.info("relay_connecting", url=self._sanitise(connect_url))
                try:
                    async with websockets.connect(
                        connect_url,
                        ssl=ssl_ctx,
                        max_size=8 * 1024 * 1024,  # 8 MB ceiling per frame
                        open_timeout=10,
                        close_timeout=5,
                        ping_interval=20,
                        ping_timeout=20,
                    ) as ws:
                        logger.info("relay_connected")
                        backoff = 1.0
                        await self._session(ws)
                except (OSError, ConnectionClosed) as e:
                    logger.warning(
                        "relay_connection_lost",
                        error=str(e),
                        retry_in_s=backoff,
                    )
                except Exception:
                    logger.exception("relay_connection_error")

                if self._stop.is_set():
                    break
                await asyncio.wait(
                    [asyncio.create_task(asyncio.sleep(backoff)),
                     asyncio.create_task(self._stop.wait())],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                backoff = min(backoff * 2, 30.0)
        finally:
            self._stop_hw()

    def _auth_url(self) -> str:
        if not self.token:
            return self.url
        sep = "&" if "?" in self.url else "?"
        return f"{self.url}{sep}token={self.token}"

    @staticmethod
    def _sanitise(url: str) -> str:
        # Strip the token from logs.
        if "token=" not in url:
            return url
        head, _, _ = url.partition("token=")
        return head + "token=***"

    async def _session(self, ws) -> None:
        # Hello + initial privacy state.
        await ws.send(
            Frame.hello(version="0.1.0", hostname=socket.gethostname()).encode()
        )
        if self._gpio is not None:
            self._privacy_engaged = self._gpio.privacy_engaged
            await ws.send(
                Frame.gpio_event("privacy", state=self._privacy_engaged).encode()
            )

        send_audio = asyncio.create_task(self._pump_audio(ws))
        send_video = asyncio.create_task(self._pump_video(ws))
        recv_loop = asyncio.create_task(self._recv_loop(ws))

        done, pending = await asyncio.wait(
            [send_audio, send_video, recv_loop],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        for t in pending:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        for t in done:
            exc = t.exception()
            if exc:
                logger.debug("relay_session_task_error", exc_info=exc)

    async def _pump_audio(self, ws) -> None:
        if self._mic is None:
            await asyncio.Event().wait()
            return
        async for chunk in self._mic.chunks():
            if self._privacy_engaged:
                continue
            try:
                await ws.send(Frame.audio_in(chunk).encode())
            except Exception:
                logger.debug("audio_send_failed", exc_info=True)
                return

    async def _pump_video(self, ws) -> None:
        if self._cam is None:
            await asyncio.Event().wait()
            return
        period = 1.0 / max(1, self.camera_fps)
        last_sent: Optional[bytes] = None
        while not self._stop.is_set():
            await asyncio.sleep(period)
            if self._privacy_engaged:
                continue
            jpeg = self._cam.latest()
            if jpeg is None or jpeg is last_sent:
                continue
            last_sent = jpeg
            try:
                await ws.send(Frame.video(jpeg).encode())
            except Exception:
                logger.debug("video_send_failed", exc_info=True)
                return

    async def _recv_loop(self, ws) -> None:
        async for raw in ws:
            if isinstance(raw, str):
                # Server side speaks binary; strings are unexpected.
                logger.debug("relay_unexpected_text", size=len(raw))
                continue
            try:
                frame = Frame.decode(raw)
            except ValueError:
                continue

            if frame.op == Op.AUDIO_OUT:
                if self._speaker is not None:
                    await asyncio.get_running_loop().run_in_executor(
                        None, self._speaker.play, frame.payload
                    )
            elif frame.op == Op.LED:
                if self._gpio is not None:
                    try:
                        info = frame.json()
                        self._gpio.set_led(
                            float(info.get("r", 0.0)),
                            float(info.get("g", 0.0)),
                            float(info.get("b", 0.0)),
                        )
                    except Exception:
                        logger.debug("led_apply_failed", exc_info=True)
            elif frame.op == Op.RELAY:
                if self._gpio is not None:
                    try:
                        info = frame.json()
                        self._gpio.set_relay(int(info["pin"]), bool(info.get("state")))
                    except Exception:
                        logger.debug("relay_apply_failed", exc_info=True)
            elif frame.op == Op.PING:
                pass
            else:
                logger.debug("relay_pi_unhandled_op", op=int(frame.op))

    def stop(self) -> None:
        self._stop.set()


# ---------- CLI --------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="palantir-pi-relay")
    p.add_argument(
        "--laptop",
        required=False,
        default=os.environ.get("PALANTIR_LAPTOP_URL", ""),
        help="WS URL on the laptop, e.g. wss://laptop.local:8080/relay/ws "
        "(or set PALANTIR_LAPTOP_URL).",
    )
    p.add_argument(
        "--token",
        required=False,
        default=os.environ.get("PALANTIR_AUTH_TOKEN", ""),
        help="Auth bearer token; usually set via PALANTIR_AUTH_TOKEN.",
    )
    p.add_argument(
        "--camera-type",
        choices=("auto", "csi", "usb"),
        default="auto",
        help="Camera backend.  'csi' = ribbon-cable Pi camera via picamera2; "
        "'usb' = cv2.VideoCapture; 'auto' tries csi then falls back to usb.",
    )
    p.add_argument(
        "--camera",
        type=int,
        default=0,
        help="cv2 video device index (only used when --camera-type=usb or auto fallback).",
    )
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--fps", type=int, default=15)
    p.add_argument("--jpeg-quality", type=int, default=75)
    p.add_argument("--mic", type=int, default=None, help="sounddevice input index")
    p.add_argument("--audio-sr", type=int, default=DEFAULT_AUDIO_SR_HZ)
    p.add_argument("--audio-chunk-ms", type=int, default=30)
    p.add_argument("--no-video", action="store_true")
    p.add_argument("--no-audio-in", action="store_true")
    p.add_argument("--no-audio-out", action="store_true")
    p.add_argument("--no-gpio", action="store_true")
    p.add_argument(
        "--insecure",
        action="store_true",
        help="Skip TLS verification (needed for the self-signed laptop cert).",
    )
    return p


def main() -> None:
    setup_logging("pi-relay")
    args = _build_argparser().parse_args()

    if not args.laptop:
        print(
            "error: --laptop URL is required (or set PALANTIR_LAPTOP_URL)",
            file=sys.stderr,
        )
        sys.exit(2)

    client = PiRelayClient(
        url=args.laptop,
        token=args.token or None,
        camera_type=args.camera_type,
        camera_device=args.camera,
        camera_width=args.width,
        camera_height=args.height,
        camera_fps=args.fps,
        jpeg_quality=args.jpeg_quality,
        mic_device=args.mic,
        audio_chunk_ms=args.audio_chunk_ms,
        audio_sr=args.audio_sr,
        no_video=args.no_video,
        no_audio_in=args.no_audio_in,
        no_audio_out=args.no_audio_out,
        no_gpio=args.no_gpio,
        verify_tls=not args.insecure,
    )

    logger.info("pi_relay_starting", host=platform.node(), python=sys.version.split()[0])

    loop = asyncio.new_event_loop()
    try:
        try:
            import signal as _signal

            def _shutdown(*_a: object) -> None:
                client.stop()

            for sig in (_signal.SIGTERM, _signal.SIGINT):
                try:
                    loop.add_signal_handler(sig, _shutdown)
                except NotImplementedError:
                    pass
        except Exception:
            pass
        loop.run_until_complete(client.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
