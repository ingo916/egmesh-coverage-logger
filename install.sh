#!/bin/bash
# EGMESH Coverage Logger - Installer
# Copyright (c) 2026 Ingo Azarvand / EGMESH.NET - https://egmesh.net
set -e
echo "  EGMESH Coverage Logger - Installer"
INSTALL_DIR="$HOME/egmesh_logger"
LOG_DIR="$HOME/egmesh_logs"
mkdir -p "$INSTALL_DIR" "$LOG_DIR"
cp app.py "$INSTALL_DIR/app.py"
cp index.html "$INSTALL_DIR/index.html"
[ -f heatmap.py ] && cp heatmap.py "$INSTALL_DIR/heatmap.py"
mkdir -p "$INSTALL_DIR/config"
[ -f config/repeaters.json.example ] && cp config/repeaters.json.example "$INSTALL_DIR/config/"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
"$INSTALL_DIR/venv/bin/pip" install flask pymc-core pynmea2 pyserial folium pandas --quiet
sudo bash -c "cat > /etc/systemd/system/egmesh.service << SVCEOF
[Unit]
Description=EGMESH Coverage Logger
After=network.target

[Service]
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF"
sudo systemctl daemon-reload
sudo systemctl enable egmesh
echo "  Done! Run: sudo ./setup_hotspot.sh"
echo "  Created by Ingo Azarvand for EGMESH.NET - Elk Grove, CA"
