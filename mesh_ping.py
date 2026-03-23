#!/usr/bin/env python3
"""
EGMESH Coverage Logger — Ping/SNR Module (v3)
USB Serial only — auto-detects MeshCore companion radio.

Features:
    - Auto-detect serial port (skips GPS devices)
    - Ping repeater via flood echo (RX_LOG_DATA)
    - Read device info and radio config
    - Scan contacts from device
    - Configure radio settings and reboot from web UI
    - Falls back to raw public key if repeater not in device contacts
"""

import asyncio
import csv
import glob
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from meshcore import MeshCore, EventType

logger = logging.getLogger("mesh_ping")


def _shell(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def _find_serial_port():
    """Auto-detect the MeshCore serial port, skipping GPS devices."""
    ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    for port in ports:
        rc, info, _ = _shell(f"udevadm info -q property {port} 2>/dev/null")
        info = info.lower()
        if any(tag in info for tag in [
            "id_vendor_id=1546", "u-blox", "ublox", "gps", "gnss", "sirf", "nmea",
        ]):
            logger.debug("Skipping GPS device: %s", port)
            continue
        logger.debug("Candidate MeshCore port: %s", port)
        return port
    return None


def _get_config_path():
    """Find repeaters.json — check working dir first, then ~/egmesh_logger/."""
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repeaters.json")
    if os.path.exists(local):
        return local
    fallback = os.path.expanduser("~/egmesh_logger/repeaters.json")
    if os.path.exists(fallback):
        return fallback
    return local  # default to local even if not found yet


# ──────────────────────────────────────────────────
#  Configuration
# ──────────────────────────────────────────────────
REPEATER_NAME = "EG SE RAK4631 RPTR"
CSV_PATH = Path("ping_log.csv")
SERIAL_BAUD = 115200
ECHO_TIMEOUT = 15
RECONNECT_COOLDOWN = 10


class PingResult:
    __slots__ = (
        "success", "timestamp", "rtt_s",
        "snr", "rssi", "noise_floor",
        "echo_snr", "echo_rssi",
        "serial_port",
        "lat", "lon", "error",
    )

    def __init__(self):
        self.success = False
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.rtt_s = None
        self.snr = None
        self.rssi = None
        self.noise_floor = None
        self.echo_snr = None
        self.echo_rssi = None
        self.serial_port = None
        self.lat = None
        self.lon = None
        self.error = None

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    def csv_row(self):
        return [
            self.timestamp, self.success, self.rtt_s,
            self.snr, self.rssi, self.noise_floor,
            self.echo_snr, self.echo_rssi,
            self.serial_port,
            self.lat, self.lon, self.error or "",
        ]

    CSV_HEADER = [
        "timestamp", "success", "rtt_s",
        "snr", "rssi", "noise_floor",
        "echo_snr", "echo_rssi",
        "serial_port",
        "lat", "lon", "error",
    ]


class MeshPinger:
    """
    Connects to MeshCore companion via USB serial.
    Auto-detects serial port. Supports ping, radio config, and contact scanning.
    """

    def __init__(
        self,
        repeater_name: str = REPEATER_NAME,
        csv_path: Path = CSV_PATH,
    ):
        self.repeater_name = repeater_name
        self.csv_path = Path(csv_path)

        self._mc: Optional[MeshCore] = None
        self._serial_port: Optional[str] = None
        self._repeater_contact = None
        self._self_info = None
        self._lock = asyncio.Lock()
        self._last_connect_attempt = 0.0

        self._rx_log_event = None
        self._rx_log_data = None
        self._rx_log_subscribed = False

        self._ensure_csv()

    # ──────────────────────────────────────────
    #  Public API — Ping
    # ──────────────────────────────────────────

    async def ping_repeater(
        self,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        repeater_name: Optional[str] = None,
    ) -> PingResult:
        """Ping the repeater and return SNR/RSSI result.
        If repeater_name is provided, uses that instead of the default."""
        result = PingResult()
        result.lat = lat
        result.lon = lon

        # Update target repeater if a different name was passed
        if repeater_name and repeater_name != self.repeater_name:
            self.repeater_name = repeater_name
            self._repeater_contact = None  # force re-lookup

        try:
            mc = await self._ensure_connected()
            result.serial_port = self._serial_port
            repeater = await self._find_repeater(mc)
            self._setup_rx_log_listener(mc)

            self._rx_log_data = None
            self._rx_log_event = asyncio.Event()

            t0 = time.monotonic()
            send_result = await mc.commands.send_statusreq(repeater)

            if send_result.type == EventType.ERROR:
                result.error = f"send_statusreq failed: {send_result.payload}"
                logger.warning("Ping send failed: %s", result.error)
                self._log_csv(result)
                return result

            logger.info("Status request sent via %s, waiting for echo...",
                        self._serial_port)

            try:
                await asyncio.wait_for(
                    self._rx_log_event.wait(), timeout=ECHO_TIMEOUT)
                result.rtt_s = round(time.monotonic() - t0, 2)
                result.success = True
                if self._rx_log_data:
                    result.echo_snr = self._rx_log_data.get("snr")
                    result.echo_rssi = self._rx_log_data.get("rssi")
                    logger.info("Echo: snr=%.2f rssi=%d rtt=%.1fs",
                                result.echo_snr or 0, result.echo_rssi or 0,
                                result.rtt_s)
            except asyncio.TimeoutError:
                result.rtt_s = round(time.monotonic() - t0, 2)
                result.error = f"No echo within {ECHO_TIMEOUT}s"
                logger.warning("Ping timeout")

            try:
                radio = await mc.commands.get_stats_radio()
                if radio.type != EventType.ERROR and isinstance(radio.payload, dict):
                    result.snr = radio.payload.get("last_snr")
                    result.rssi = radio.payload.get("last_rssi")
                    result.noise_floor = radio.payload.get("noise_floor")
            except Exception as e:
                logger.debug("get_stats_radio failed: %s", e)

        except Exception as e:
            result.error = str(e)
            logger.error("Ping exception: %s", e, exc_info=True)
            await self._disconnect()

        self._log_csv(result)
        return result

    # ──────────────────────────────────────────
    #  Public API — Device Info & Radio Config
    # ──────────────────────────────────────────

    async def get_device_info(self) -> dict:
        """
        Get current device info including radio settings.
        Returns dict with radio_freq, radio_bw, radio_sf, radio_cr, name, etc.
        """
        mc = await self._ensure_connected()

        info_event = asyncio.Event()
        info_data = {}

        async def on_self_info(event):
            info_data.update(event.payload)
            info_event.set()

        sub_handle = mc.subscribe(EventType.SELF_INFO, on_self_info)

        try:
            await mc.commands.send_appstart()
            await asyncio.wait_for(info_event.wait(), timeout=5)
            self._self_info = info_data
        except asyncio.TimeoutError:
            if self._self_info:
                info_data = self._self_info
            else:
                info_data = {"error": "No response from device"}
        finally:
            mc.unsubscribe(sub_handle)

        return info_data

    async def configure_radio(self, freq: float, bw: float, sf: int, cr: int) -> dict:
        """
        Set radio parameters and reboot the device.
        Disconnects the current session (device reboots).
        Returns {"ok": True} or {"ok": False, "error": "..."}.
        """
        port = self._serial_port or _find_serial_port()
        if not port:
            return {"ok": False, "error": "No MeshCore device found"}

        await self._disconnect()
        await asyncio.sleep(1)

        radio_str = f"{freq},{bw},{sf},{cr}"
        logger.info("Configuring radio: %s on %s", radio_str, port)

        try:
            commands = f"/set radio {radio_str}\n/reboot\nquit\n"
            rc, stdout, stderr = _shell(
                f'echo "{commands}" | timeout 15 meshcli -s {port}',
                timeout=20,
            )
            logger.info("meshcli output: %s %s", stdout[:200], stderr[:200])

            if "Error" in stdout or "Error" in stderr:
                return {"ok": False, "error": f"meshcli error: {stdout} {stderr}"}

        except Exception as e:
            return {"ok": False, "error": f"Failed to run meshcli: {e}"}

        logger.info("Waiting for device to reboot...")
        await asyncio.sleep(6)

        try:
            mc = await self._ensure_connected()
            info = await self.get_device_info()
            return {
                "ok": True,
                "radio_freq": info.get("radio_freq"),
                "radio_bw": info.get("radio_bw"),
                "radio_sf": info.get("radio_sf"),
                "radio_cr": info.get("radio_cr"),
            }
        except Exception as e:
            return {
                "ok": True,
                "warning": f"Radio set but reconnect failed: {e}. May need to replug USB.",
            }

    # ──────────────────────────────────────────
    #  Public API — Contact Scanning
    # ──────────────────────────────────────────

    async def scan_contacts(self) -> list:
        """
        Get all contacts from the device.
        Returns list of dicts with name, key, type.
        """
        mc = await self._ensure_connected()
        result = await mc.commands.get_contacts()

        if result.type == EventType.ERROR:
            logger.warning("scan_contacts error: %s", result.payload)
            return []

        contacts = []
        for pubkey, contact in result.payload.items():
            contacts.append({
                "name": contact.get("adv_name", ""),
                "key": pubkey,
                "type": contact.get("type", 0),
                "type_name": {0: "Node", 1: "Client", 2: "Repeater", 3: "Bridge"}.get(
                    contact.get("type", 0), "Unknown"
                ),
                "lat": contact.get("adv_lat"),
                "lon": contact.get("adv_lon"),
            })

        logger.info("Scanned %d contacts", len(contacts))
        return contacts

    # ──────────────────────────────────────────
    #  Public properties
    # ──────────────────────────────────────────

    async def disconnect(self):
        await self._disconnect()

    @property
    def is_connected(self) -> bool:
        return self._mc is not None

    # ──────────────────────────────────────────
    #  RX_LOG_DATA listener
    # ──────────────────────────────────────────

    def _setup_rx_log_listener(self, mc: MeshCore):
        if self._rx_log_subscribed:
            return

        async def on_rx_log(event):
            payload = event.payload
            logger.debug("RX_LOG_DATA: snr=%s rssi=%s type=%s",
                         payload.get("snr"), payload.get("rssi"),
                         payload.get("payload_typename"))
            if self._rx_log_event and not self._rx_log_event.is_set():
                self._rx_log_data = payload
                self._rx_log_event.set()

        mc.subscribe(EventType.RX_LOG_DATA, on_rx_log)
        self._rx_log_subscribed = True

    # ──────────────────────────────────────────
    #  Connection management
    # ──────────────────────────────────────────

    async def _ensure_connected(self) -> MeshCore:
        async with self._lock:
            if self._mc is not None:
                return self._mc

            elapsed = time.monotonic() - self._last_connect_attempt
            if elapsed < RECONNECT_COOLDOWN:
                wait = RECONNECT_COOLDOWN - elapsed
                await asyncio.sleep(wait)

            self._last_connect_attempt = time.monotonic()

            port = _find_serial_port()
            if not port:
                raise ConnectionError(
                    "No MeshCore device found. "
                    "Plug in the companion radio via USB. "
                    "Flash USB Companion firmware from https://flasher.meshcore.co"
                )

            logger.info("Connecting to %s @ %d baud", port, SERIAL_BAUD)
            mc = await MeshCore.create_serial(port, SERIAL_BAUD)

            if mc is None:
                raise ConnectionError(
                    f"Device on {port} did not respond. "
                    "Make sure it is running USB Companion firmware."
                )

            self._mc = mc
            self._serial_port = port
            self._repeater_contact = None
            self._rx_log_subscribed = False
            logger.info("Connected via serial on %s", port)
            return mc

    async def _find_repeater(self, mc: MeshCore):
        """
        Find repeater by name in device contacts.
        Falls back to raw public key from repeaters.json if not found.
        """
        if self._repeater_contact is not None:
            return self._repeater_contact

        # Try device contact list first
        result = await mc.commands.get_contacts()
        if result.type != EventType.ERROR:
            contacts = result.payload
            # Exact match
            for pubkey, contact in contacts.items():
                name = contact.get("adv_name", "")
                if name == self.repeater_name:
                    self._repeater_contact = contact
                    logger.info("Found repeater (exact): %s", name)
                    return contact
            # Substring match
            for pubkey, contact in contacts.items():
                name = contact.get("adv_name", "")
                if self.repeater_name.lower() in name.lower():
                    self._repeater_contact = contact
                    logger.info("Found repeater (substring): %s", name)
                    return contact

        # Fall back to raw public key from repeaters.json
        cfg_path = _get_config_path()
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            active = next(
                (r for r in cfg["repeaters"] if r["id"] == cfg.get("active")),
                None,
            )
            if active and active.get("key"):
                key = active["key"]
                logger.info("Using raw public key for '%s': %s...",
                            active["name"], key[:16])
                self._repeater_contact = key
                return key
        except Exception as e:
            logger.debug("Could not read repeaters.json: %s", e)

        raise RuntimeError(
            f"Repeater '{self.repeater_name}' not found in contacts or config"
        )

    async def _disconnect(self):
        mc = self._mc
        self._mc = None
        self._serial_port = None
        self._repeater_contact = None
        self._self_info = None
        self._rx_log_subscribed = False
        if mc is not None:
            try:
                await mc.disconnect()
            except Exception:
                pass

    # ──────────────────────────────────────────
    #  CSV logging
    # ──────────────────────────────────────────

    def _ensure_csv(self):
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="") as f:
                csv.writer(f).writerow(PingResult.CSV_HEADER)

    def _log_csv(self, result: PingResult):
        try:
            with open(self.csv_path, "a", newline="") as f:
                csv.writer(f).writerow(result.csv_row())
        except Exception as e:
            logger.error("CSV write failed: %s", e)


# ──────────────────────────────────────────────────
#  Standalone test
# ──────────────────────────────────────────────────
async def _standalone_test():
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(name)-12s  %(levelname)-7s  %(message)s",
    )
    pinger = MeshPinger()

    print("\n--- Device Info ---")
    info = await pinger.get_device_info()
    for k in ["name", "radio_freq", "radio_bw", "radio_sf", "radio_cr", "tx_power"]:
        print(f"  {k:15s} = {info.get(k)}")

    print("\n--- Contacts ---")
    contacts = await pinger.scan_contacts()
    for c in contacts:
        print(f"  {c['name']:30s}  {c['type_name']:10s}  {c['key'][:16]}...")

    print("\n--- Ping ---")
    r = await pinger.ping_repeater(lat=38.4088, lon=-121.3716)
    print(f"  success={r.success}  snr={r.echo_snr}  rssi={r.echo_rssi}  rtt={r.rtt_s}s")

    await pinger.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(_standalone_test())
    except KeyboardInterrupt:
        print("\nInterrupted.")
