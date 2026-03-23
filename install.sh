#!/bin/bash
# EGMESH Coverage Logger - Installer
# Copyright (c) 2026 Ingo Azarvand / EGMESH.NET - https://egmesh.net
set -e

echo ""
echo "  EGMESH Coverage Logger - Installer v2.1"
echo "  https://egmesh.net"
echo ""

INSTALL_DIR="$HOME/egmesh_logger"
LOG_DIR="$HOME/egmesh_logs"

# ── Create directories ────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR" "$LOG_DIR"

# ── Copy application files ────────────────────────────────────────────────────
cp app.py "$INSTALL_DIR/app.py"
cp mesh_ping.py "$INSTALL_DIR/mesh_ping.py"
cp index.html "$INSTALL_DIR/index.html"
[ -f heatmap.py ] && cp heatmap.py "$INSTALL_DIR/heatmap.py"
mkdir -p "$INSTALL_DIR/config"
[ -f config/repeaters.json.example ] && cp config/repeaters.json.example "$INSTALL_DIR/config/"

# ── Initialize repeaters.json if not present ──────────────────────────────────
if [ ! -f "$INSTALL_DIR/repeaters.json" ]; then
    echo '{"active": null, "repeaters": []}' > "$INSTALL_DIR/repeaters.json"
fi

# ── Python virtual environment ────────────────────────────────────────────────
echo "  Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
echo "  Installing dependencies..."
"$INSTALL_DIR/venv/bin/pip" install flask meshcore pynmea2 pyserial folium pandas --quiet

# ── Systemd service ───────────────────────────────────────────────────────────
echo "  Installing systemd service..."
sudo bash -c "cat > /etc/systemd/system/egmesh.service << SVCEOF
[Unit]
Description=EGMESH Coverage Logger
After=network.target

[Service]
Type=simple
User=root
Environment=HOME=$HOME
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF"

sudo systemctl daemon-reload
sudo systemctl enable egmesh

echo ""
echo "  ┌─────────────────────────────────────────────────────┐"
echo "  │  Install complete!                                  │"
echo "  │                                                     │"
echo "  │  SETUP:                                             │"
echo "  │                                                     │"
echo "  │  1. Flash USB Companion firmware on your device:    │"
echo "  │     https://flasher.meshcore.co                     │"
echo "  │                                                     │"
echo "  │  2. Plug the device into the Pi via USB             │"
echo "  │     (auto-detected, no pairing needed)              │"
echo "  │                                                     │"
echo "  │  3. Configure radio (one time):                     │"
echo "  │     pip install meshcore-cli                        │"
echo "  │     meshcli -s /dev/ttyACM0                         │"
echo "  │     /set radio 910.525,125,9,5                      │"
echo "  │     /reboot                                         │"
echo "  │                                                     │"
echo "  │  4. Edit mesh_ping.py:                              │"
echo "  │     REPEATER_NAME = 'your repeater name'            │"
echo "  │                                                     │"
echo "  │  5. Set up Wi-Fi hotspot (optional):                │"
echo "  │     sudo ./setup_hotspot.sh                         │"
echo "  │                                                     │"
echo "  │  6. Start the service:                              │"
echo "  │     sudo systemctl start egmesh                     │"
echo "  │                                                     │"
echo "  │  Web UI: http://192.168.4.1:5000                    │"
echo "  └─────────────────────────────────────────────────────┘"
echo ""
echo "  Created by Ingo Azarvand for EGMESH.NET - Elk Grove, CA"
echo ""
