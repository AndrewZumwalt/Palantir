# Palantir — Project Handoff

A wall-mounted AI classroom assistant: camera + microphone + speaker, runs
six cooperating microservices, talks back via Claude (Anthropic) with voice
input/output and face + voice identification.

This document captures the project's state, how to run it, and the warts
that someone picking it up tomorrow needs to know about before they hit
them themselves.

---

## 1. What works (and what doesn't)

### ✅ Currently working

| Area | Status |
|---|---|
| Six-service architecture (audio, vision, brain, tts, eventlog, web) | Stable, all heartbeats green on the dashboard |
| Memurai-backed Redis broker | Required, port 6379. Pub/sub works across processes (a previous in-process-fakeredis default was broken on Windows). |
| Face enrollment via browser (Subject Intake) | Works in `-LocalAudio` mode (vision in relay = browser owns camera) |
| Face recognition (live) | Works after clicking **START SCANNING** on Diagnostics |
| Voice enrollment | Works once `pip install -e ".[speaker]"` has been run (downloads ECAPA-TDNN model on first use) |
| Wake-word trigger | `hey_jarvis` built-in model, threshold 0.5 |
| **Wake-word visual feedback** | Top-right "WAKE WORD HEARD" badge flashes on every detection |
| STT (Whisper) | `faster-whisper` base.en, downloads on first utterance (~140 MB) |
| LLM (Anthropic / Groq fallback) | Configure via Settings → API Keys panel; brain hot-reloads on save |
| TTS (Edge Neural voices) | `en-US-AriaNeural`, natural-sounding, free (needs internet) |
| Chat tab with text input | `/chat` route — type messages, see history, polls every 1.5 s |
| Live camera feed | `/camera` route — MJPEG stream with detection overlays |
| Runtime camera-mode toggle | START / STOP SCANNING on Diagnostics — swap browser ↔ vision capture without launcher restart |
| Identity context to LLM | Brain assembles `[SPEAKER] Andrew` + `[VISIBLE PERSONS]` in system prompt; LLM addresses by name |
| Visual question routing | "describe", "what is he wearing", "tell me about" etc. → cloud vision call with live frame |

### ⚠️ Known limitations / known not-working

| Thing | Why | Workaround |
|---|---|---|
| Wake word "Hey Palantir" | Not a built-in `openwakeword` model; custom training takes hours of synthetic data + a CPU/GPU run | Use "Hey Jarvis" (default) or switch to `alexa` / `hey_mycroft` / `hey_rhasspy` via `config/default.toml` |
| Camera-sharing with browser | Windows refuses to share a webcam between processes | Solved by toggling vision between relay (browser owns) and local (vision owns) at runtime |
| Custom wake-word model | See above | Drop a trained `.onnx` into `.dev-data/models/` and set `audio.wake_word_model = "./.dev-data/models/your.onnx"` |
| Anthropic doesn't have TTS | Their API is text + vision only | Already addressed — using Microsoft Edge neural TTS (free, no API key) |
| First voice / face / STT use is slow | Models download from HuggingFace on first call (faster-whisper ~140 MB, ECAPA ~25 MB, insightface ~125 MB) | Warm them up by running the launcher once and exercising each path before the demo |
| Brief look-away thrashes the Present panel | `attendance.exit_timeout_seconds = 10` for demo responsiveness | Raise it (to 30-60) in `config/default.toml` if you'd rather have stickier presence |

---

## 2. First-time setup

### Required tools (Windows)

1. **Python 3.11** — `winget install -e --id Python.Python.3.11`
2. **Node.js LTS** — `winget install -e --id OpenJS.NodeJS.LTS`
3. **Microsoft Visual C++ Build Tools** (for `insightface`) — `winget install --id Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"`
4. **Memurai Developer** (Windows Redis) — `winget install -e --id Memurai.MemuraiDeveloper` (admin)

### Install Python deps

```powershell
cd C:\Users\Andrew\Palantir
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[voice,face,speaker]"
```

Pip extras:
- `voice` — Anthropic SDK + Groq SDK + faster-whisper + openwakeword + piper-tts + edge-tts + miniaudio
- `face` — insightface + onnxruntime (needs MSVC compiler)
- `speaker` — speechbrain + torch + torchaudio (heavy — ~500 MB download)

### Run the launcher

```powershell
cd C:\Users\Andrew\Palantir
powershell -ExecutionPolicy Bypass -File .\scripts\start-laptop.ps1 -LocalAudio
```

Flags:
- `-LocalAudio` — audio service uses the laptop mic (default for the demo path)
- `-LocalVision` — vision service uses the laptop camera at startup (blocks browser enrollment; toggle from Diagnostics instead)
- `-LocalMode` — shorthand for both
- `-Reinstall` — force `pip install -e .` (default skips it if `palantir` is already installed)
- `-NoFakeRedis` — honor an externally-set `REDIS_URL` instead of pointing at Memurai

The launcher:
1. Verifies Python 3.11 venv
2. Kills any leftover `palantir-*` processes
3. Pre-checks Memurai is reachable on 127.0.0.1:6379
4. Builds the frontend (auto-installs `node_modules` if missing)
5. Generates a self-signed TLS cert in `.dev-data/tls/` on first start
6. Spawns the six services with logs in `.dev-data/*.log`

---

## 3. Day-of demo procedure

1. **Start launcher** — `powershell -ExecutionPolicy Bypass -File .\scripts\start-laptop.ps1 -LocalAudio`
2. **Open** `https://localhost:8080`, accept the self-signed cert warning
3. **Settings → API Keys** — paste your Anthropic key, Save. Brain reloads automatically (no restart needed)
4. **Subject Intake** — for each person:
   - Enter name, role (student/teacher/admin/guest), submit
   - Accept consent
   - Capture 10 face photos (the counter shows progress)
   - Record 5 voice samples (~3 s each)
   - Wait for "Archive" step — face + voice are now in the DB and the running services have been hot-reloaded
5. **Diagnostics → Room scanning → START SCANNING** — vision takes over the camera for live recognition
6. **Speak the wake word** ("Hey Jarvis") — top-right corner flashes the **WAKE WORD HEARD** badge with confidence percent
7. **Ask a question** — e.g. "What am I wearing?" or "Describe him." Claude receives:
   - Your enrolled name (from voice match)
   - The visible people in frame (from face recognition)
   - The current camera frame (for visual questions)
   - The previous N turns of conversation history
8. **Optional fallback** — Comms Channel tab (`/chat`) accepts typed messages if voice isn't cooperating

---

## 4. Architecture

### Six services, all listening to Redis

```
              ┌─────────────────────────────────────────────────────┐
              │                      MEMURAI (Redis)                │
              │   pub/sub channels + ephemeral state hashes/keys    │
              └─────────────────────────────────────────────────────┘
                 ▲           ▲             ▲          ▲          ▲
                 │           │             │          │          │
   ┌────────┐    │    ┌─────────────┐     │   ┌─────────────┐   │
   │ audio  │────┘    │   vision    │─────┘   │    brain    │───┘
   │ (mic)  │         │  (camera)   │         │  (LLM)      │
   └────────┘         └─────────────┘         └─────────────┘
        │ AUDIO_WAKE        │ VISION_FACES         │ BRAIN_RESPONSE
        │ AUDIO_UTTERANCE   │ VISION_ENGAGEMENT    │ BRAIN_ACTION
        │                   │ VISION_OBJECTS       │
        ▼                   ▼                      ▼
   ┌────────┐         ┌────────────┐         ┌──────────┐
   │  tts   │         │  eventlog  │         │   web    │
   │(speaker│         │ (sqlite)   │         │ (FastAPI │
   │ output)│         │            │         │  + SPA)  │
   └────────┘         └────────────┘         └──────────┘
```

- **audio** — `sounddevice` → openwakeword (`hey_jarvis`) → VAD → faster-whisper (STT) → speechbrain (speaker ID) → publishes `AUDIO_UTTERANCE` with text + speaker
- **vision** — `cv2.VideoCapture` → insightface (faces) → engagement classifier → YOLO (objects, if installed) → publishes `VISION_*` channels, keeps `state:visible_persons` + `state:latest_frame` warm
- **brain** — subscribes to `AUDIO_UTTERANCE`, runs `IdentityLinker.link(voice_id, visible_faces)`, calls `ContextBuilder` (assembles `[SPEAKER]`, `[VISIBLE PERSONS]`, recent conversation, memory), calls LLM, publishes `BRAIN_RESPONSE`. Visual questions hit `CloudVision.analyze_frame(...)` with the live camera frame
- **tts** — subscribes to `BRAIN_RESPONSE`, synthesizes via Edge TTS (default) or Piper (fallback), plays via `sounddevice`
- **eventlog** — subscribes to `EVENTS_LOG`, writes to SQLite, handles attendance roll-up
- **web** — FastAPI app: REST endpoints (`/api/...`), WebSocket bridge from Redis to browser (`/ws`), MJPEG camera stream (`/api/vision/stream`), serves frontend SPA from `frontend/dist/`

### Front-end pages

| Route | Component | Purpose |
|---|---|---|
| `/` | DashboardPage | Microservice health, recent visible persons, last events |
| `/camera` | CameraPage | Live MJPEG + SVG overlays (faces, objects, engagement) |
| `/chat` | ChatPage | Conversation history + text input box |
| `/attendance` | AttendancePanel | Present / departed roll-up from `attendance_records` |
| `/engagement` | EngagementPage | Per-person engagement timeline |
| `/enrollment` | EnrollmentWizard | Face + voice intake flow |
| `/automation` | AutomationPage | Rule CRUD |
| `/events` | EventLogPage | Raw event stream |
| `/system` | SystemPage | Diagnostics, START/STOP SCANNING toggle, power cycle |
| `/settings` | SettingsPage | API keys, privacy, retention |

---

## 5. Where things live

```
Palantir/
├── config/                    # TOML config + per-environment overrides
│   ├── default.toml           # base defaults
│   └── development.toml       # dev overrides (mostly empty)
├── frontend/
│   ├── src/components/        # One folder per page + ui/ primitives
│   ├── src/hooks/             # useWebSocket, etc.
│   ├── src/api/               # client.ts + websocket.ts singletons
│   └── dist/                  # built bundle (auto-rebuilt by launcher)
├── scripts/
│   ├── start-laptop.ps1       # Windows multi-service launcher
│   ├── install-pi-relay.sh    # Pi-side install
│   └── run-fake-redis.py      # TCP fakeredis (Linux/Mac only; broken on Windows pub/sub)
├── src/palantir/
│   ├── audio/                 # capture, wake_word, vad, stt, speaker_id
│   ├── vision/                # capture, face_detector/recognizer, engagement, cloud_vision
│   ├── brain/                 # service, llm_client, identity_linker, context_builder, automation
│   ├── tts/                   # service, edge_engine (default), piper_engine, audio_output
│   ├── web/
│   │   ├── main.py            # FastAPI app + WebSocket
│   │   └── routers/           # chat, vision_stream, enrollment, system, settings, etc.
│   ├── relay/                 # Pi-side client + protocol
│   ├── config.py              # dataclass-based config
│   ├── db.py                  # SQLite init + migrations
│   ├── models.py              # Pydantic models shared across services
│   └── redis_client.py        # connection + Channels/Keys constants
├── systemd/                   # service units for Pi deployment
├── tests/                     # pytest suite (run with `pytest`)
├── .dev-data/                 # logs, models cache, SQLite DB, TLS certs
└── pyproject.toml
```

---

## 6. Troubleshooting cookbook

### "All services NO HEARTBEAT"
- Memurai isn't running. `winget install -e --id Memurai.MemuraiDeveloper`, then make sure the `Memurai` service is started (`Get-Service Memurai*`).
- If Memurai is running but you see `connection refused` in the logs, check that nothing else has port 6379 bound.

### "Could not access camera" in the Enrollment wizard
- Vision service has the camera open. Go to Diagnostics → **STOP SCANNING**.
- Or close any other camera-using app (Teams, Zoom, Camera).
- The error message will tell you which case it is — `NotReadableError` means in use, `NotAllowedError` means permission denied.

### "503 Service Unavailable: Speaker ID model not available"
- speechbrain isn't installed. Run `.\.venv\Scripts\python.exe -m pip install -e ".[speaker]"` (heavy — pulls torch).

### "FACE DETECTION OFFLINE — backend has no insightface"
- insightface isn't installed. Needs the MSVC Build Tools (`Microsoft.VisualStudio.2022.BuildTools` with `Microsoft.VisualStudio.Workload.VCTools`).
- After MSVC install: `.\.venv\Scripts\python.exe -m pip install -e ".[face]"`

### Camera page is a black box (overlays still firing)
- Vision service is in relay mode but you're not running a Pi. Click **START SCANNING** on Diagnostics.
- If the box is black AFTER start scanning, check `palantir-vision.err.log` for `Failed to open camera device 0`.

### Wake word never seems to trigger
- The visual indicator (top-right "WAKE WORD HEARD" badge) tells you definitively whether the audio service heard it.
- If it never flashes:
  - Audio service might not be in `-LocalAudio` mode (default with no flag is relay, which expects a Pi)
  - `wake_word_threshold` might be too strict — drop it lower in `config/default.toml`
  - Background noise is too loud — try in a quieter setting

### LLM responds but seems to ignore the camera
- Verify your question matches one of the visual triggers in `brain/service.py:_is_visual_question`. Currently includes: "describe", "tell me about him/her/them", "what is he/she/they wearing", "who is he/she/that", "is he/she/they ...", "wearing", "holding", "doing", etc.
- If you're using a different phrasing, add it to the trigger list.

### "Andrew is here" lingers when nobody is in frame
- The vision prune timeout is 3 s of unseen → drop from `state:visible_persons`. If yours is hanging longer, check the vision service is actually running (`palantir-vision.out.log` should show `visible_pruned` lines).
- On startup, vision now wipes `state:visible_persons` / `state:present_persons` — stale entries from a dead process don't survive.

### Newly enrolled person not recognized
- Enrollment endpoint auto-publishes `SYSTEM_RELOAD` on completion → vision/audio `reload_profiles()` runs → new embedding is in the cache.
- If that didn't fire, click **POWER CYCLE** at the bottom of any dashboard page.

### Launcher fails with "WinError 32: file in use"
- A previous `palantir-*` service is still running (often elevated, from an admin shell). Find it: `Get-Process palantir-*`. Kill: `Stop-Process -Name palantir-* -Force` (run as admin if Stop-Process returns Access Denied).
- The launcher tries to kill leftovers automatically at the top — but if a stuck process predates that, you may need to clear it manually once.

---

## 7. Configuration cheat sheet

`config/default.toml` is the source of truth. Edit, restart the launcher to pick up changes.

| Section | Key | Default | What it does |
|---|---|---|---|
| `[camera]` | `width` / `height` | `1920` / `1080` | Bumps the pixels-per-face. Drop to 640×480 on slow Pi hardware |
| `[audio]` | `wake_word_threshold` | `0.5` | 0.0-1.0; lower = more sensitive |
| `[audio]` | `wake_word_model` | `"hey_jarvis"` | Built-in name OR path to a custom `.onnx` |
| `[audio]` | `stt_model` | `"base.en"` | faster-whisper model size (`tiny.en`, `small.en`, `medium.en`...) |
| `[identity]` | `face_match_threshold` | `0.4` | Lower = more permissive face matching |
| `[identity]` | `voice_match_threshold` | `0.65` | Lower = more permissive voice matching |
| `[tts]` | `engine` | `"edge"` | `"edge"` (neural, needs internet) or `"piper"` (local, robotic) |
| `[tts]` | `voice` | `"en-US-AriaNeural"` | Edge voice id; full list via `edge-tts --list-voices` |
| `[attendance]` | `exit_timeout_seconds` | `10` | Time-without-face before a person is marked exited |
| `[automation]` | `enabled` | `true` | Whether automation rules fire on events |

Environment-variable overrides for the launcher set in `start-laptop.ps1`:
- `PALANTIR_RELAY_MODE` — per-service: `local` or `relay` (launcher sets this per-service)
- `REDIS_URL` — `redis://127.0.0.1:6379/0` by default
- `PALANTIR_AUTH_TOKEN` — `devtoken` by default (set via `-AuthToken` flag)
- `PALANTIR_TLS_CERT_FILE` / `PALANTIR_TLS_KEY_FILE` — self-signed certs auto-generated in `.dev-data/tls/`

---

## 8. The future-work list

Things worth doing if you have time after the demo:

1. **Train a real "Hey Palantir" wake word.** Use openwakeword's synthetic-data training notebook with `edge-tts` to generate positives. ~hours on CPU.
2. **Replace the polling chat history with a WebSocket push.** `BRAIN_RESPONSE` is already on Redis; route it to the WebSocket bridge and the Comms Channel page can drop the 1.5 s poll.
3. **Persist the full LLM context per turn.** `conversations` table has a `context` column that's currently unused — populate it from `ContextBuilder.build()` for full request transparency.
4. **Per-voice/face confidence on the visible-persons panel.** The data is there; just needs a small UI badge.
5. **Better wake-word UX.** Optional audio chime in `WakeIndicator` to mimic Alexa's "ding" — pair it with the visual flash so the user knows even when not looking at the screen.
6. **Pi deployment.** `scripts/install-pi-relay.sh` is the Pi-side installer; the Pi connects to the laptop via the `/relay/ws` WebSocket. Switch the launcher to default (no `-LocalAudio`) once the Pi is plugged in.

---

## 9. Recent commits worth knowing about

```
d6b2944  Wake-word visual feedback + speechbrain dep clarification
9696f6f  Natural TTS via Edge + camera live feed fix + wake-word config
62c8ecb  Merge remote-tracking branch 'origin/main' into claude/sleepy-bose-d99542
ddf1e1b  Demo readiness: Memurai broker, chat tab, runtime camera mode, live UI
813fd80  Live camera feed at 1080p with detection overlays for troubleshooting
f8e6fb3  Code-review pass: four real bugs surfaced and fixed
```

The `ddf1e1b` commit body is the best single read for understanding why the launcher works the way it does (Memurai, leftover-process cleanup, split LocalAudio/LocalVision, runtime camera toggle).

---

*Last updated: 2026-05-12*
