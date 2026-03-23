# EGMESH Coverage Logger

A turnkey LoRa mesh network coverage testing and heatmap tool
built for community mesh networks running MeshCore firmware.

**Created by Ingo Azarvand for [EGMESH.NET](https://egmesh.net)**
Elk Grove, California

---

## What it does

Runs on a Raspberry Pi with a MeshCore companion radio connected via USB.
Broadcasts its own WiFi hotspot so you can connect your phone while
driving. Live web dashboard shows real-time SNR ping results and GPS
coordinates. After your drive, generate an interactive HTML heatmap
overlaid on OpenStreetMap street maps.

**How ping measurement works:** The logger sends a status request through
the mesh via flood routing. The repeater re-broadcasts the packet, and the
companion radio captures the echo. The SNR and RSSI from that echo tell you
the signal quality between your current location and the repeater.

---

## Hardware required

| Component | Details |
|---|---|
| Raspberry Pi | Pi 4 or Pi 5, any RAM variant |
| MeshCore companion radio | RAK4631, Heltec, T-Beam, etc. — USB Companion firmware |
| USB GPS dongle | u-blox NEO-6M/7 or compatible |
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
| `mesh_ping.py` | Serial ping module — auto-detects USB, captures SNR/RSSI |
| `index.html` | Web dashboard UI |
| `heatmap.py` | Post-drive heatmap generator |
| `install.sh` | One-step installer |
| `setup_hotspot.sh` | Wi-Fi hotspot setup for field use |
| `egmesh.service` | systemd unit for auto-start on boot |

---

## Quick start

### 1. Flash USB Companion firmware

Go to [flasher.meshcore.co](https://flasher.meshcore.co), select your
device, choose **Companion USB**, and flash it.

### 2. Install

```bash
git clone https://github.com/ingo916/egmesh-coverage-logger.git
cd egmesh-coverage-logger
chmod +x install.sh && ./install.sh
```

### 3. Plug in the hardware

Connect the companion radio and GPS dongle to the Pi via USB.
The serial port is auto-detected — no pairing or configuration needed.

### 4. Configure the radio (one time)

```bash
pip install meshcore-cli
meshcli -s /dev/ttyACM0
```

Inside meshcli, set your radio parameters and wait for the repeater:

```
/set radio 910.525,125,9,5
/reboot
```

Reconnect and verify the repeater appears:

```bash
meshcli -s /dev/ttyACM0
/set manual_add_contacts off
/contacts
```

Wait for the repeater to show up, then `/quit`.

### 5. Configure the repeater name

Edit `mesh_ping.py` and set your repeater name:

```python
REPEATER_NAME = "EG SE RAK4631 RPTR"    # Change to your repeater's name
```

### 6. Set up Wi-Fi hotspot (optional, for field use)

```bash
sudo ./setup_hotspot.sh
sudo reboot
```

Connect phone to WiFi **EGMESH-LOGGER** (password: egmesh2025)

### 7. Start

```bash
sudo systemctl start egmesh
```

Open browser: **http://192.168.4.1:5000**

---

## Testing the ping manually

```bash
cd ~/egmesh_logger
source venv/bin/activate
python3 mesh_ping.py
```

You should see:

```
Trying serial: /dev/ttyACM0 @ 115200 baud
Connected via SERIAL on /dev/ttyACM0
Echo received: snr=10.75  rssi=-41  rtt=0.5s
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

### "No MeshCore device found"

Make sure the companion radio is plugged in via USB and running
**USB Companion** firmware (not BLE Companion). Check:

```bash
ls /dev/ttyACM* /dev/ttyUSB*
```

You should see at least two ports — one for the radio, one for GPS.

### "Device did not respond"

The device may be running BLE Companion firmware instead of USB Companion.
Reflash from [flasher.meshcore.co](https://flasher.meshcore.co) —
select **Companion USB**.

### 0 contacts / repeater not found

The repeater needs to advertise before it appears. Connect with meshcli
and wait a few minutes:

```bash
meshcli -s /dev/ttyACM0
/set print_adverts on
/set manual_add_contacts off
/contacts
```

### GPS port changed

If the USB ports swap after replugging, the GPS port in `app.py` may
need updating. Check which port is which:

```bash
udevadm info -q property /dev/ttyACM0 | grep MODEL
udevadm info -q property /dev/ttyACM1 | grep MODEL
```

Update `GPS_PORT` in `app.py` to match the u-blox device.

---

## Adapting for your community

Edit `mesh_ping.py` to set your repeater name.
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
