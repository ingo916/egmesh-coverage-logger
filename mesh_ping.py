#!/usr/bin/env python3
"""
EGMESH Coverage Logger — Ping/SNR Module (v2)
Integrates with app.py Flask app to measure SNR to repeater and log to CSV.

How it works:
    Repeater firmware doesn't support binary status requests, so we use a
    different approach:

    1. Subscribe to RX_LOG_DATA events (fired for every received packet)
    2. Send send_statusreq to the repeater (triggers a flood packet)
    3. The repeater re-broadcasts our packet — we receive the echo
    4. The RX_LOG_DATA event from that echo contains SNR + RSSI
    5. get_stats_radio confirms local radio's last_snr / last_rssi

    This gives us the signal quality between our location and the repeater,
    which is exactly what a coverage logger needs.

Usage from Flask:
    from mesh_ping import MeshPinger
    pinger = MeshPinger()
    result = await pinger.ping_repeater(lat=38.41, lon=-121.37)
"""

import asyncio
import csv
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
    """Run shell command silently, return stdout."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def _prep_ble(address: str):
    """
    Prepare BlueZ for a clean connection.
    Clears stale GATT cache and disconnects lingering sessions.
    This is the fix for 'failed to discover services' on Pi 4.
    """
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


# ──────────────────────────────────────────────────
#  Configuration
# ──────────────────────────────────────────────────
BLE_ADDRESS = "E8:3F:59:DD:2F:F8"
BLE_NAME = "EGMESH-LOGGER"
REPEATER_NAME = "EG SE RAK4631 RPTR"
CSV_PATH = Path("ping_log.csv")
BLE_CONNECT_RETRIES = 3
BLE_RETRY_DELAY = 4
ECHO_TIMEOUT = 15             # seconds to wait for repeater echo
RECONNECT_COOLDOWN = 10


class PingResult:
    """Structured result from a single ping."""
    __slots__ = (
        "success", "timestamp", "rtt_s",
        "snr", "rssi", "noise_floor",
        "echo_snr", "echo_rssi",
        "lat", "lon", "error",
    )

    def __init__(self):
        self.success = False
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.rtt_s = None
        self.snr = None           # from get_stats_radio (last packet heard)
        self.rssi = None          # from get_stats_radio
        self.noise_floor = None   # local noise floor
        self.echo_snr = None      # from RX_LOG_DATA (repeater echo)
        self.echo_rssi = None     # from RX_LOG_DATA
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
            self.lat, self.lon, self.error or "",
        ]

    CSV_HEADER = [
        "timestamp", "success", "rtt_s",
        "snr", "rssi", "noise_floor",
        "echo_snr", "echo_rssi",
        "lat", "lon", "error",
    ]


class MeshPinger:
    """
    Manages BLE connection to MeshCore companion and pings a repeater.

    Call await ping_repeater(lat, lon) to get a PingResult.
    The BLE connection is lazy-initialized and auto-reconnects.
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
        self._lock = asyncio.Lock()
        self._last_connect_attempt = 0.0

        # RX_LOG_DATA capture state
        self._rx_log_event = None       # asyncio.Event, set when echo arrives
        self._rx_log_data = None        # captured RX_LOG_DATA payload
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
        """
        Ping the repeater and return SNR/RSSI result.

        Sends a status request via flood routing, waits for the
        repeater's echo (RX_LOG_DATA), then reads local radio stats.
        """
        result = PingResult()
        result.lat = lat
        result.lon = lon

        try:
            mc = await self._ensure_connected()
            repeater = await self._find_repeater(mc)
            self._setup_rx_log_listener(mc)

            # Reset capture state
            self._rx_log_data = None
            self._rx_log_event = asyncio.Event()

            # Send status request — this floods through the repeater
            t0 = time.monotonic()
            send_result = await mc.commands.send_statusreq(repeater)

            if send_result.type == EventType.ERROR:
                result.error = f"send_statusreq failed: {send_result.payload}"
                logger.warning("Ping send failed: %s", result.error)
                self._log_csv(result)
                return result

            logger.info("Status request sent, waiting for repeater echo...")

            # Wait for RX_LOG_DATA (repeater echo)
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

            # Also read local radio stats for confirmation
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
        """Cleanly shut down the BLE connection."""
        await self._disconnect()

    @property
    def is_connected(self) -> bool:
        return self._mc is not None

    # ──────────────────────────────────────────
    #  RX_LOG_DATA listener
    # ──────────────────────────────────────────

    def _setup_rx_log_listener(self, mc: MeshCore):
        """Subscribe to RX_LOG_DATA once per connection."""
        if self._rx_log_subscribed:
            return

        async def on_rx_log(event):
            payload = event.payload
            logger.debug("RX_LOG_DATA: snr=%s rssi=%s path=%s type=%s",
                         payload.get("snr"), payload.get("rssi"),
                         payload.get("path"), payload.get("payload_typename"))
            # Capture the first echo after we send
            if self._rx_log_event and not self._rx_log_event.is_set():
                self._rx_log_data = payload
                self._rx_log_event.set()

        mc.subscribe(EventType.RX_LOG_DATA, on_rx_log)
        self._rx_log_subscribed = True
        logger.debug("Subscribed to RX_LOG_DATA")

    # ──────────────────────────────────────────
    #  Connection management
    # ──────────────────────────────────────────

    async def _ensure_connected(self) -> MeshCore:
        """Return an active MeshCore instance, reconnecting if needed."""
        async with self._lock:
            if self._mc is not None:
                return self._mc

            elapsed = time.monotonic() - self._last_connect_attempt
            if elapsed < RECONNECT_COOLDOWN:
                wait = RECONNECT_COOLDOWN - elapsed
                logger.debug("Reconnect cooldown: waiting %.1fs", wait)
                await asyncio.sleep(wait)

            self._last_connect_attempt = time.monotonic()
            _prep_ble(self.ble_address)

            for attempt in range(1, BLE_CONNECT_RETRIES + 1):
                try:
                    logger.info("BLE connect attempt %d/%d  addr=%s",
                                attempt, BLE_CONNECT_RETRIES, self.ble_address)
                    mc = await MeshCore.create_ble(self.ble_address)
                    self._mc = mc
                    self._repeater_contact = None
                    self._rx_log_subscribed = False
                    logger.info("BLE connected via MAC")
                    return mc
                except Exception as e:
                    logger.warning("BLE connect attempt %d failed: %s", attempt, e)
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
                self._repeater_contact = None
                self._rx_log_subscribed = False
                logger.info("BLE connected via name scan")
                return mc
            except Exception as e:
                raise ConnectionError(
                    f"BLE connection failed after {BLE_CONNECT_RETRIES} retries: {e}"
                ) from e

    async def _find_repeater(self, mc: MeshCore):
        """Find repeater contact, loading contacts if needed."""
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
        """Tear down BLE connection."""
        mc = self._mc
        self._mc = None
        self._repeater_contact = None
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
