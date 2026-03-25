#!/usr/bin/env python3
"""
EGMESH WiFi Toggle — GPIO button to switch between hotspot and home WiFi.

Hardware: Momentary push button between GPIO 17 (pin 11) and GND (pin 9).
  - Single press  → Field mode (hotspot: EGMESH-LOGGER / egmesh2025)
  - Double press   → Home mode (reconnect to saved WiFi)

Run as systemd service: egmesh-wifi.service
"""

import subprocess
import time
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)
logger = logging.getLogger("wifi_toggle")

# ── CONFIG ────────────────────────────────────────────────────────────────────
BUTTON_PIN     = 17
HOTSPOT_SSID   = "EGMESH-LOGGER"
HOTSPOT_PASS   = "egmesh2025"
DOUBLE_PRESS_T = 0.8   # seconds to wait for a second press
DEBOUNCE_T     = 0.05  # debounce time in seconds
LED_PATH       = "/sys/class/leds/ACT/brightness"  # Pi activity LED

# ── HELPERS ───────────────────────────────────────────────────────────────────
def shell(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def blink_led(times, speed=0.15):
    """Blink the Pi activity LED as feedback."""
    try:
        # Save current trigger
        with open("/sys/class/leds/ACT/trigger", "r") as f:
            old_trigger = f.read().strip()
            # Find the active trigger (between [ ])
            import re
            m = re.search(r'\[(\w+)\]', old_trigger)
            old_trigger = m.group(1) if m else "mmc0"

        # Switch to manual control
        with open("/sys/class/leds/ACT/trigger", "w") as f:
            f.write("none")

        for _ in range(times):
            with open(LED_PATH, "w") as f:
                f.write("1")
            time.sleep(speed)
            with open(LED_PATH, "w") as f:
                f.write("0")
            time.sleep(speed)

        # Restore original trigger
        with open("/sys/class/leds/ACT/trigger", "w") as f:
            f.write(old_trigger)
    except Exception:
        pass

def is_hotspot_active():
    """Check if hotspot is currently running."""
    rc, out, _ = shell("nmcli -t -f TYPE,STATE connection show --active")
    return "wifi" in out.lower() and "hotspot" in out.lower()

def switch_to_hotspot():
    """Activate WiFi hotspot for field use."""
    logger.info("Switching to HOTSPOT mode...")
    blink_led(2, 0.1)  # 2 quick blinks = switching

    # Disconnect any existing WiFi
    shell("nmcli device disconnect wlan0")
    time.sleep(2)

    # Start hotspot
    rc, out, err = shell(
        f'nmcli device wifi hotspot ifname wlan0 ssid "{HOTSPOT_SSID}" password "{HOTSPOT_PASS}"'
    )
    if rc == 0:
        logger.info("HOTSPOT active: %s (pw: %s)", HOTSPOT_SSID, HOTSPOT_PASS)
        blink_led(3, 0.3)  # 3 slow blinks = hotspot on
    else:
        logger.error("Hotspot failed: %s %s", out, err)
        blink_led(10, 0.05)  # rapid blinks = error

def switch_to_wifi():
    """Reconnect to home WiFi."""
    logger.info("Switching to HOME WiFi...")
    blink_led(2, 0.1)  # 2 quick blinks = switching

    # Stop hotspot
    shell("nmcli device disconnect wlan0")
    time.sleep(2)

    # Reconnect to saved WiFi
    rc, out, err = shell("nmcli device connect wlan0")
    if rc == 0:
        time.sleep(3)
        rc2, ip, _ = shell("hostname -I")
        logger.info("WiFi connected: %s", ip)
        blink_led(5, 0.2)  # 5 blinks = wifi on
    else:
        logger.error("WiFi reconnect failed: %s %s", out, err)
        blink_led(10, 0.05)  # rapid blinks = error

# ── MAIN LOOP (gpiozero) ─────────────────────────────────────────────────────
def main():
    try:
        from gpiozero import Button
    except ImportError:
        logger.error("gpiozero not installed. Run: sudo apt-get install -y python3-gpiozero")
        sys.exit(1)

    button = Button(BUTTON_PIN, pull_up=True, bounce_time=DEBOUNCE_T)
    logger.info("WiFi toggle ready on GPIO %d", BUTTON_PIN)
    logger.info("  Single press = hotspot | Double press = home WiFi")

    # Show current state
    if is_hotspot_active():
        logger.info("  Current mode: HOTSPOT")
    else:
        rc, ip, _ = shell("hostname -I")
        logger.info("  Current mode: WiFi (%s)", ip)

    while True:
        # Wait for first press
        button.wait_for_press()
        t1 = time.monotonic()
        button.wait_for_release()

        # Wait to see if there's a second press
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

        # Cooldown to avoid accidental re-trigger
        time.sleep(3)


if __name__ == "__main__":
    main()
