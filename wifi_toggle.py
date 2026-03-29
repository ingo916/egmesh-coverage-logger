#!/usr/bin/env python3
"""
EGMESH WiFi Toggle — GPIO button to switch between hotspot and home WiFi.

Hardware:
  Button    : GPIO 17 (pin 11) → GND (pin 9)          — momentary push button
  Green LED : GPIO 27 (pin 13) → 330Ω resistor → GND  — home WiFi indicator
  Blue LED  : GPIO 22 (pin 15) → 330Ω resistor → GND  — hotspot indicator

  LEDs are optional — if not wired, the pins are driven silently with no effect.

  - Single press  → Field mode (hotspot: EGMESH-LOGGER / egmesh2025)
  - Double press  → Home mode (reconnect to saved WiFi)

LED behavior:
  Home WiFi active  : Green slow pulse, Blue off
  Hotspot active    : Blue slow pulse,  Green off
  Switching         : Both blink quickly
  Error             : Both rapid blink

Run as systemd service: egmesh-wifi.service
"""

import subprocess
import time
import sys
import logging
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)
logger = logging.getLogger("wifi_toggle")

# ── CONFIG ────────────────────────────────────────────────────────────────────
BUTTON_PIN     = 17
GREEN_LED_PIN  = 27   # Home WiFi indicator (pin 13)
BLUE_LED_PIN   = 22   # Hotspot indicator   (pin 15)
HOTSPOT_SSID   = "EGMESH-LOGGER"
HOTSPOT_PASS   = "egmesh2025"
DOUBLE_PRESS_T = 0.8   # seconds to wait for a second press
DEBOUNCE_T     = 0.05  # debounce time in seconds

# ── LED CONTROL ───────────────────────────────────────────────────────────────
green_led     = None
blue_led      = None
_pulse_thread = None
_pulse_stop   = threading.Event()

def _init_leds():
    global green_led, blue_led
    from gpiozero import LED
    green_led = LED(GREEN_LED_PIN)
    blue_led  = LED(BLUE_LED_PIN)
    green_led.off()
    blue_led.off()
    logger.info("LEDs ready — GPIO %d (green), GPIO %d (blue)",
                GREEN_LED_PIN, BLUE_LED_PIN)

def _stop_pulse():
    global _pulse_thread
    _pulse_stop.set()
    if _pulse_thread and _pulse_thread.is_alive():
        _pulse_thread.join(timeout=2)
    _pulse_stop.clear()
    _pulse_thread = None

def _pulse_loop(led, speed=0.8):
    """Slowly pulse a single LED until stopped."""
    while not _pulse_stop.is_set():
        led.on()
        _pulse_stop.wait(speed)
        led.off()
        _pulse_stop.wait(speed)

def start_pulse(mode):
    """Start slow pulse — green for wifi, blue for hotspot."""
    global _pulse_thread
    _stop_pulse()
    green_led.off()
    blue_led.off()
    led = green_led if mode == "wifi" else blue_led
    _pulse_thread = threading.Thread(target=_pulse_loop, args=(led,), daemon=True)
    _pulse_thread.start()

def blink(times, speed=0.15, leds="both"):
    """Blink LEDs as feedback."""
    _stop_pulse()
    targets = []
    if leds in ("both", "green"): targets.append(green_led)
    if leds in ("both", "blue"):  targets.append(blue_led)
    for _ in range(times):
        for led in targets: led.on()
        time.sleep(speed)
        for led in targets: led.off()
        time.sleep(speed)

# ── HELPERS ───────────────────────────────────────────────────────────────────
def shell(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def is_hotspot_active():
    rc, out, _ = shell("nmcli -t -f TYPE,STATE connection show --active")
    return "wifi" in out.lower() and "hotspot" in out.lower()

# ── WIFI SWITCHING ────────────────────────────────────────────────────────────
def switch_to_hotspot():
    """Activate WiFi hotspot for field use."""
    logger.info("Switching to HOTSPOT mode...")
    blink(2, 0.1)  # 2 quick blinks = switching

    shell("nmcli device disconnect wlan0")
    time.sleep(2)

    rc, out, err = shell(
        f'nmcli device wifi hotspot ifname wlan0 ssid "{HOTSPOT_SSID}" password "{HOTSPOT_PASS}"'
    )
    if rc == 0:
        shell("sudo nmcli connection modify Hotspot connection.autoconnect no")
        logger.info("HOTSPOT active: %s (pw: %s)", HOTSPOT_SSID, HOTSPOT_PASS)
        start_pulse("hotspot")  # Blue slow pulse
    else:
        logger.error("Hotspot failed: %s %s", out, err)
        blink(10, 0.05)  # rapid blinks = error

def switch_to_wifi():
    """Reconnect to home WiFi."""
    logger.info("Switching to HOME WiFi...")
    blink(2, 0.1)  # 2 quick blinks = switching

    shell("nmcli connection down Hotspot")
    time.sleep(1)
    shell("nmcli connection delete Hotspot")
    time.sleep(2)

    rc, out, err = shell("nmcli device connect wlan0")
    if rc == 0:
        time.sleep(3)
        rc2, ip, _ = shell("hostname -I")
        logger.info("WiFi connected: %s", ip)
        start_pulse("wifi")  # Green slow pulse
    else:
        logger.error("WiFi reconnect failed: %s %s", out, err)
        blink(10, 0.05)  # rapid blinks = error

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
def main():
    try:
        from gpiozero import Button
    except ImportError:
        logger.error("gpiozero not installed. Run: sudo apt-get install -y python3-gpiozero")
        sys.exit(1)

    _init_leds()

    button = Button(BUTTON_PIN, pull_up=True, bounce_time=DEBOUNCE_T)
    logger.info("WiFi toggle ready on GPIO %d", BUTTON_PIN)
    logger.info("  Single press = hotspot | Double press = home WiFi")

    # Set initial LED state
    if is_hotspot_active():
        logger.info("  Current mode: HOTSPOT")
        start_pulse("hotspot")
    else:
        rc, ip, _ = shell("hostname -I")
        logger.info("  Current mode: WiFi (%s)", ip)
        start_pulse("wifi")

    while True:
        button.wait_for_press()
        t1 = time.monotonic()
        button.wait_for_release()

        press_count = 1
        deadline = t1 + DOUBLE_PRESS_T
        while time.monotonic() < deadline:
            if button.is_pressed:
                press_count += 1
                button.wait_for_release()
                break
            time.sleep(0.02)

        if press_count >= 2:
            switch_to_wifi()
        else:
            switch_to_hotspot()

        time.sleep(3)


if __name__ == "__main__":
    main()
