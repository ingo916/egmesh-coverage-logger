# EGMESH Coverage Logger

A turnkey LoRa mesh network coverage testing and heatmap tool
built for community mesh networks running MeshCore firmware.

**Created by Ingo Azarvand for [EGMESH.NET](https://egmesh.net)**
Elk Grove, California

---

## What it does

Runs on a Raspberry Pi 4 with a RAK WisBlock RAK4631 BLE companion radio.
Broadcasts its own WiFi hotspot so you can connect your phone while
driving. Live web dashboard shows real-time SNR ping results and GPS
coordinates. After your drive, generate an interactive HTML heatmap
overlaid on OpenStreetMap street maps.

**How ping measurement works:** The logger sends a status request through
the mesh via flood routing. The repeater re-broadcasts the packet, and the
companion radio captures the echo. The RX_LOG_DATA event from that echo
contains the SNR and RSSI values — this is the signal quality between your
current location and the repeater.

---

## Hardware required

| Component | Details |
|---|---|
| Raspberry Pi 4 | Any RAM variant |
| RAK WisBlock RAK4631 | MeshCore companion firmware, BLE connection |
| USB GPS dongle | u-blox NEO-6M or compatible |
| MicroSD card | 16GB+ |
| Power | USB-C power bank or 12V car adapter |

---

## EGMESH.NET Radio Settings

| Setting | Value |
|---|---|
| Frequency | 910.525 MHz |
| Bandwidth | 125 kHz |
| Spreading Factor | SF9 |
| Coding Rate | 4/5 |
| TX Power | 22 dBm |

---

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask web app — routes, GPS reading, logger loop |
| `mesh_ping.py` | BLE ping module — persistent connection, SNR/RSSI capture |
| `index.html` | Web dashboard UI |
| `heatmap.py` | Post-drive heatmap generator |
| `install.sh` | One-step installer |
| `setup_hotspot.sh` | Wi-Fi hotspot setup for field use |
| `egmesh.service` | systemd unit for auto-start on boot |

---

## Quick start

### 1. Install

```bash
git clone https://github.com/ingo916/egmesh-coverage-logger.git
cd egmesh-coverage-logger
chmod +x install.sh && ./install.sh
```

### 2. Pair the BLE companion radio

The RAK4631 connects to the Pi via Bluetooth Low Energy. Pair it once:

```bash
bluetoothctl
> agent KeyboardOnly
> default-agent
> scan on
```

Wait for your MeshCore device to appear (e.g. `MeshCore-EGMESH-LOGGER`), then:

```
> pair <MAC_ADDRESS>
```

Enter the PIN when prompted (default: `123456`), then:

```
> trust <MAC_ADDRESS>
> scan off
> exit
```

Verify it's paired:

```bash
bluetoothctl devices Paired
```

### 3. Configure the radio

Edit `mesh_ping.py` and set your device's MAC address and repeater name:

```python
BLE_ADDRESS = "E8:3F:59:DD:2F:F8"      # Your companion's MAC
REPEATER_NAME = "EG SE RAK4631 RPTR"    # Name of the repeater to ping
```

If your companion radio needs its frequency/bandwidth configured, use meshcli:

```bash
pip install meshcore-cli
meshcli -a <MAC_ADDRESS>
/set radio 910.525,125,9,5
/reboot
```

Reconnect and verify the repeater appears in contacts:

```bash
meshcli -a <MAC_ADDRESS>
/contacts
```

### 4. Set up Wi-Fi hotspot (optional, for field use)

```bash
sudo ./setup_hotspot.sh
sudo reboot
```

Connect phone to WiFi **EGMESH-LOGGER** (password: egmesh2025)

### 5. Start the service

```bash
sudo systemctl start egmesh
```

Open browser: **http://192.168.4.1:5000**

---

## Testing the ping manually

Run the ping module standalone to verify BLE and radio are working:

```bash
cd ~/egmesh_logger
source venv/bin/activate
sudo venv/bin/python3 mesh_ping.py
```

You should see output like:

```
Echo received: snr=10.75  rssi=-46  rtt=0.9s
Local radio: snr=10.75  rssi=-46  noise=-104
```

---

## Generate a heatmap after your drive

```bash
cd ~/egmesh_logger
source venv/bin/activate
python heatmap.py
```

Opens an interactive HTML heatmap overlaid on OpenStreetMap.
Internet connection required when viewing.

---

## Troubleshooting

### BLE won't connect / "service discovery failed"

This is usually a stale GATT cache on the Pi. Fix it:

```bash
bluetoothctl remove <MAC_ADDRESS>
sudo systemctl restart bluetooth
```

Power cycle the RAK4631, then re-pair (see step 2 above).

### 0 contacts after connecting

The repeater needs to advertise before it appears in the contact list.
Connect with meshcli and wait a few minutes:

```bash
meshcli -a <MAC_ADDRESS>
/set print_adverts on
/contacts
```

### "No repeater selected" when starting

Make sure the app runs with the correct HOME directory:

```bash
sudo -E env HOME=/home/pi venv/bin/python3 app.py
```

The systemd service handles this automatically.

### BlueZ 5.82+ command changes

If `bluetoothctl paired-devices` doesn't work, use:

```
bluetoothctl devices Paired
```

---

## Adapting for your community

Edit `mesh_ping.py` to set your BLE address and repeater name.
Change `LORA_FREQ` in `app.py` to your region's frequency.
Update the header in `index.html` to your network name.

Common frequencies:

- US EGMESH.NET: 910.525 MHz / BW125 / SF9 / CR4/5
- EU868: 868.0 MHz / BW125 / SF9

---

## Credit and attribution

Created by **Ingo Azarvand** for **EGMESH.NET**
Volunteer community LoRa mesh network — Elk Grove, California
Emergency preparedness and neighborhood resilience.

If you use or adapt this tool, please credit **EGMESH.NET**
and **Ingo Azarvand, Elk Grove, CA** in your documentation.

https://egmesh.net

---

## License

Copyright (c) 2026 Ingo Azarvand / EGMESH.NET
See LICENSE for full terms.
