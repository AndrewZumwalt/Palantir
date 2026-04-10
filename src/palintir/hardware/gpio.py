"""GPIO abstraction for Raspberry Pi hardware control.

Handles the privacy switch, status LED, and relay outputs.
Falls back to mock implementations on non-Pi hardware.
"""

from __future__ import annotations

import asyncio
from typing import Callable

import structlog

logger = structlog.get_logger()

try:
    from gpiozero import RGBLED, Button, OutputDevice

    _GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    _GPIO_AVAILABLE = False


class MockButton:
    """Mock button for development on non-Pi machines."""

    def __init__(self, pin: int):
        self.pin = pin
        self.when_pressed: Callable | None = None
        self.when_released: Callable | None = None
        self.is_pressed = False


class MockRGBLED:
    """Mock RGB LED for development."""

    def __init__(self, red: int, green: int, blue: int):
        self.color = (0, 0, 0)

    def close(self) -> None:
        pass


class MockOutputDevice:
    """Mock relay/output for development."""

    def __init__(self, pin: int):
        self.pin = pin
        self._value = False

    def on(self) -> None:
        self._value = True
        logger.debug("mock_gpio_on", pin=self.pin)

    def off(self) -> None:
        self._value = False
        logger.debug("mock_gpio_off", pin=self.pin)

    @property
    def value(self) -> bool:
        return self._value

    def close(self) -> None:
        pass


# GPIO Pin assignments (configurable via config in future)
PRIVACY_SWITCH_PIN = 17
LED_RED_PIN = 22
LED_GREEN_PIN = 27
LED_BLUE_PIN = 24


class HardwareController:
    """Manages all GPIO hardware: privacy switch, status LED, relays."""

    def __init__(self):
        self._privacy_callbacks: list[Callable[[bool], None]] = []
        self._relays: dict[int, OutputDevice | MockOutputDevice] = {}

        if _GPIO_AVAILABLE:
            self._privacy_switch = Button(PRIVACY_SWITCH_PIN, pull_up=True, bounce_time=0.1)
            self._led = RGBLED(LED_RED_PIN, LED_GREEN_PIN, LED_BLUE_PIN)
        else:
            logger.warning("gpio_not_available", reason="using mock hardware")
            self._privacy_switch = MockButton(PRIVACY_SWITCH_PIN)
            self._led = MockRGBLED(LED_RED_PIN, LED_GREEN_PIN, LED_BLUE_PIN)

        self._privacy_switch.when_pressed = self._on_privacy_pressed
        self._privacy_switch.when_released = self._on_privacy_released

    def on_privacy_toggle(self, callback: Callable[[bool], None]) -> None:
        """Register a callback for privacy switch state changes.

        Args:
            callback: Called with True when privacy enabled, False when disabled.
        """
        self._privacy_callbacks.append(callback)

    def _on_privacy_pressed(self) -> None:
        for cb in self._privacy_callbacks:
            cb(True)

    def _on_privacy_released(self) -> None:
        for cb in self._privacy_callbacks:
            cb(False)

    def set_led_color(self, r: float, g: float, b: float) -> None:
        """Set the status LED color (0.0-1.0 per channel)."""
        self._led.color = (r, g, b)

    def set_led_active(self) -> None:
        """Green: system active and running."""
        self.set_led_color(0, 1, 0)

    def set_led_processing(self) -> None:
        """Yellow: processing a request."""
        self.set_led_color(1, 1, 0)

    def set_led_privacy(self) -> None:
        """Red: privacy mode active."""
        self.set_led_color(1, 0, 0)

    def set_led_off(self) -> None:
        """Turn off the LED."""
        self.set_led_color(0, 0, 0)

    def get_relay(self, pin: int) -> OutputDevice | MockOutputDevice:
        """Get or create a relay output device for a GPIO pin."""
        if pin not in self._relays:
            if _GPIO_AVAILABLE:
                self._relays[pin] = OutputDevice(pin)
            else:
                self._relays[pin] = MockOutputDevice(pin)
        return self._relays[pin]

    def set_relay(self, pin: int, state: bool) -> None:
        """Set a relay output pin high (True) or low (False)."""
        relay = self.get_relay(pin)
        if state:
            relay.on()
        else:
            relay.off()
        logger.info("relay_set", pin=pin, state=state)

    def cleanup(self) -> None:
        """Release all GPIO resources."""
        for relay in self._relays.values():
            relay.close()
        self._led.close()
        logger.info("gpio_cleanup_complete")
