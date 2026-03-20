# EGMESH Coverage Logger

A turnkey LoRa mesh network coverage testing and heatmap tool
built for community mesh networks running MeshCore firmware.

**Created by Ingo Azarvand for [EGMESH.NET](https://egmesh.net)**
Elk Grove, California

---

## What it does

Runs on a headless Raspberry Pi 4 with a Waveshare SX1262 LoRa HAT.
Broadcasts its own WiFi hotspot so you can connect your phone while
driving. Live web dashboard shows real-time SNR ping results and GPS
coordinates. After your drive, generate an interactive HTML heatmap
overlaid on OpenStreetMap street maps.

---

## Hardware required

| Component | Details |
|---|---|
| Raspberry Pi 4 | Any RAM variant |
| Waveshare SX1262 LoRa HAT | 915MHz (US) |
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
| TX Power | 20 dBm |

---

## Quick start
```bash
git clone https://github.com/ingo916/egmesh-coverage-logger.git
cd egmesh-coverage-logger
chmod +x install.sh && ./install.sh
sudo ./setup_hotspot.sh
sudo reboot
```

Connect phone to WiFi **EGMESH-LOGGER** (password: egmesh2025)
Open browser: **http://192.168.4.1:5000**

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

## Adapting for your community

Change `LORA_FREQ` in `app.py` to your region's frequency.
Update the header in `index.html` to your network name.

Common frequencies:
- US EGMESH.NET: 910.525 MHz / BW125 / SF9 / CR4/5
- US narrow preset: 910.525 MHz / BW62.5 / SF7
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
