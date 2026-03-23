#!/usr/bin/env python3
"""
EGMESH Coverage Logger — Ping/SNR Module (v2.1)
Auto-detects connection type: USB Serial (zero config) or BLE (requires pairing).

Connection priority:
    1. USB Serial — scans /dev/ttyACM* and /dev/ttyUSB*, skips GPS devices
    2. BLE — falls back with retry logic and GATT cache clearing

Usage from Flask:
    from mesh_ping import MeshPinger
    pinger = MeshPinger()
    result = await pinger.ping_repeater(lat=38.41, lon=-121.37)
"""

import asyncio
import csv
import glob
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
        return r.stdout.strip()
    except Exception:
        return ""


def _prep_ble(address: str):
    """Clear stale GATT cache and disconnect lingering BLE sessions."""
    logger.debug("Preparing BLE stack for %s", address)
    _shell(f"bluetoothctl disconnect {address}", timeout=5)
    time.sleep(1)
    addr_path = address.upper().replace(":", "_")
    cache_base = "/var/lib/bluetooth"
    adapters = _shell(f"ls {cache_base}/ 2>/dev/null").splitlines()
    for adapter in adapters:
        adapter = adapter.strip()
        for suffix in ["cache", "attributes"]:
            path = f"{cache_base}/{adapter}/{addr_path}/{suffix}"
            if _shell(f"test -e {path} && echo yes") == "yes":
                logger.info("Clearing stale GATT cache: %s", path)
                _shell(f"sudo rm -rf {path}")
    time.sleep(1)


def _find_serial_ports():
    """Find candidate serial ports, skipping known GPS devices."""
    ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    candidates = []
    for port in ports:
        info = _shell(f"udevadm info -q property {port} 2>/dev/null").lower()
        # Skip GPS devices
        if any(tag in info for tag in ["1546:", "id_vendor_id=1546", "u-blox", "ublox", "gps", "gnss", "sirf", "nmea"]):
            logger.debug("Skipping GPS device: %s", port)
            continue
        candidates.append(port)
    return candidates


# ──────────────────────────────────────────────────
#  Configuration
# ──────────────────────────────────────────────────
BLE_ADDRESS = "E8:3F:59:DD:2F:F8"
BLE_NAME = "EGMESH-LOGGER"
REPEATER_NAME = "EG SE RAK4631 RPTR"
CSV_PATH = Path("ping_log.csv")
SERIAL_BAUD = 115200
BLE_CONNECT_RETRIES = 3
BLE_RETRY_DELAY = 4
ECHO_TIMEOUT = 15
RECONNECT_COOLDOWN = 10


class PingResult:
    __slots__ = (
        "success", "timestamp", "rtt_s",
        "snr", "rssi", "noise_floor",
        "echo_snr", "echo_rssi",
        "connection_type",
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
        self.connection_type = None
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
            self.connection_type,
            self.lat, self.lon, self.error or "",
        ]

    CSV_HEADER = [
        "timestamp", "success", "rtt_s",
        "snr", "rssi", "noise_floor",
        "echo_snr", "echo_rssi",
        "connection_type",
        "lat", "lon", "error",
    ]


class MeshPinger:
    """
    Auto-detects Serial or BLE connection to MeshCore companion.
    Serial is tried first (zero config), BLE as fallback.
    """

    def __init__(
        self,
        ble_address: str = BLE_ADDRESS,
        ble_name: str = BLE_NAME,
        repeater_name: str = REPEATER_NAME,
        csv_path: Path = CSV_PATH,
    ):
        self.ble_address = ble_address
        self.ble_name = ble_name
        self.repeater_name = repeater_name
        self.csv_path = Path(csv_path)

        self._mc: Optional[MeshCore] = None
        self._repeater_contact = None
        self._connection_type = None
        self._lock = asyncio.Lock()
        self._last_connect_attempt = 0.0

        self._rx_log_event = None
        self._rx_log_data = None
        self._rx_log_subscribed = False

        self._ensure_csv()

    # ──────────────────────────────────────────
    #  Public API
    # ──────────────────────────────────────────

    async def ping_repeater(
        self,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ) -> PingResult:
        result = PingResult()
        result.lat = lat
        result.lon = lon

        try:
            mc = await self._ensure_connected()
            result.connection_type = self._connection_type
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
                        self._connection_type)

            try:
                await asyncio.wait_for(
                    self._rx_log_event.wait(),
                    timeout=ECHO_TIMEOUT,
                )
                result.rtt_s = round(time.monotonic() - t0, 2)
                result.success = True

                if self._rx_log_data:
                    result.echo_snr = self._rx_log_data.get("snr")
                    result.echo_rssi = self._rx_log_data.get("rssi")
                    logger.info(
                        "Echo received: snr=%.2f  rssi=%d  rtt=%.1fs",
                        result.echo_snr or 0, result.echo_rssi or 0,
                        result.rtt_s,
                    )
            except asyncio.TimeoutError:
                result.rtt_s = round(time.monotonic() - t0, 2)
                result.error = f"No echo within {ECHO_TIMEOUT}s"
                logger.warning("Ping timeout: no RX_LOG_DATA received")

            try:
                radio = await mc.commands.get_stats_radio()
                if radio.type != EventType.ERROR and isinstance(radio.payload, dict):
                    result.snr = radio.payload.get("last_snr")
                    result.rssi = radio.payload.get("last_rssi")
                    result.noise_floor = radio.payload.get("noise_floor")
                    logger.info(
                        "Local radio: snr=%.2f  rssi=%d  noise=%d",
                        result.snr or 0, result.rssi or 0,
                        result.noise_floor or 0,
                    )
            except Exception as e:
                logger.debug("get_stats_radio failed: %s", e)

        except Exception as e:
            result.error = str(e)
            logger.error("Ping exception: %s", e, exc_info=True)
            await self._disconnect()

        self._log_csv(result)
        return result

    async def disconnect(self):
        await self._disconnect()

    @property
    def is_connected(self) -> bool:
        return self._mc is not None

    @property
    def connection_type(self) -> Optional[str]:
        return self._connection_type

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
        logger.debug("Subscribed to RX_LOG_DATA")

    # ──────────────────────────────────────────
    #  Connection management (auto-detect)
    # ──────────────────────────────────────────

    async def _ensure_connected(self) -> MeshCore:
        async with self._lock:
            if self._mc is not None:
                return self._mc

            elapsed = time.monotonic() - self._last_connect_attempt
            if elapsed < RECONNECT_COOLDOWN:
                wait = RECONNECT_COOLDOWN - elapsed
                logger.debug("Reconnect cooldown: waiting %.1fs", wait)
                await asyncio.sleep(wait)

            self._last_connect_attempt = time.monotonic()

            # Try Serial first (zero config)
            mc = await self._try_serial()
            if mc:
                return mc

            # Fall back to BLE
            mc = await self._try_ble()
            if mc:
                return mc

            raise ConnectionError(
                "Could not connect via Serial or BLE. "
                "Serial: plug in USB with serial companion firmware. "
                "BLE: pair device first with bluetoothctl."
            )

    async def _try_serial(self) -> Optional[MeshCore]:
        candidates = _find_serial_ports()
        if not candidates:
            logger.debug("No candidate serial ports found")
            return None

        for port in candidates:
            try:
                logger.info("Trying serial: %s @ %d baud", port, SERIAL_BAUD)
                mc = await MeshCore.create_serial(port, SERIAL_BAUD)
                # Verify it actually responded (create_serial returns None on failure)
                if mc is None:
                    logger.debug("Serial %s returned None", port)
                    continue
                self._mc = mc
                self._connection_type = "serial"
                self._repeater_contact = None
                self._rx_log_subscribed = False
                logger.info("Connected via SERIAL on %s", port)
                return mc
            except Exception as e:
                logger.debug("Serial %s failed: %s", port, e)
                continue

        logger.info("No serial companion found, trying BLE...")
        return None

    async def _try_ble(self) -> Optional[MeshCore]:
        _prep_ble(self.ble_address)

        for attempt in range(1, BLE_CONNECT_RETRIES + 1):
            try:
                logger.info("BLE connect attempt %d/%d  addr=%s",
                            attempt, BLE_CONNECT_RETRIES, self.ble_address)
                mc = await MeshCore.create_ble(self.ble_address)
                self._mc = mc
                self._connection_type = "ble"
                self._repeater_contact = None
                self._rx_log_subscribed = False
                logger.info("Connected via BLE")
                return mc
            except Exception as e:
                logger.warning("BLE attempt %d failed: %s", attempt, e)
                if attempt < BLE_CONNECT_RETRIES:
                    if attempt >= 2:
                        logger.info("Cycling hci0 adapter...")
                        _shell("sudo hciconfig hci0 down")
                        await asyncio.sleep(2)
                        _shell("sudo hciconfig hci0 up")
                    await asyncio.sleep(BLE_RETRY_DELAY)

        try:
            logger.info("Trying BLE name scan: %s", self.ble_name)
            mc = await MeshCore.create_ble(self.ble_name)
            self._mc = mc
            self._connection_type = "ble"
            self._repeater_contact = None
            self._rx_log_subscribed = False
            logger.info("Connected via BLE name scan")
            return mc
        except Exception as e:
            logger.warning("BLE name scan failed: %s", e)
            return None

    async def _find_repeater(self, mc: MeshCore):
        if self._repeater_contact is not None:
            return self._repeater_contact

        result = await mc.commands.get_contacts()
        if result.type == EventType.ERROR:
            raise RuntimeError(f"Failed to load contacts: {result.payload}")

        contacts = result.payload
        for pubkey, contact in contacts.items():
            name = contact.get("adv_name", "")
            if name == self.repeater_name:
                self._repeater_contact = contact
                logger.info("Found repeater (exact): %s", name)
                return contact

        for pubkey, contact in contacts.items():
            name = contact.get("adv_name", "")
            if self.repeater_name.lower() in name.lower():
                self._repeater_contact = contact
                logger.info("Found repeater (substring): %s", name)
                return contact

        raise RuntimeError(
            f"Repeater '{self.repeater_name}' not found among "
            f"{len(contacts)} contacts"
        )

    async def _disconnect(self):
        mc = self._mc
        self._mc = None
        self._repeater_contact = None
        self._connection_type = None
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

    print("\n--- Ping 1 ---")
    r1 = await pinger.ping_repeater(lat=38.4088, lon=-121.3716)
    print(f"\nResult:")
    for k in PingResult.__slots__:
        print(f"  {k:20s} = {getattr(r1, k)}")

    await asyncio.sleep(3)

    print("\n--- Ping 2 (reuses connection) ---")
    r2 = await pinger.ping_repeater(lat=38.4088, lon=-121.3716)
    print(f"\nResult:")
    for k in PingResult.__slots__:
        print(f"  {k:20s} = {getattr(r2, k)}")

    await pinger.disconnect()
    print(f"\nCSV log: {pinger.csv_path}")


if __name__ == "__main__":
    try:
        asyncio.run(_standalone_test())
    except KeyboardInterrupt:
        print("\nInterrupted.")
