#!/bin/bash
# EGMESH Coverage Logger - Installer
# Copyright (c) 2026 Ingo Azarvand / EGMESH.NET - https://egmesh.net
set -e

echo ""
echo "  EGMESH Coverage Logger - Installer v3.1"
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
"$INSTALL_DIR/venv/bin/pip" install flask meshcore meshcore-cli pynmea2 pyserial pyopenssl folium pandas --quiet

# ── SSL certificate (required for phone GPS) ─────────────────────────────────
if [ ! -f "$INSTALL_DIR/cert.pem" ]; then
    echo "  Generating SSL certificate for phone GPS..."
    openssl req -x509 -newkey rsa:2048 \
        -keyout "$INSTALL_DIR/key.pem" \
        -out "$INSTALL_DIR/cert.pem" \
        -days 3650 -nodes \
        -subj "/CN=EGMESH" 2>/dev/null
    echo "  SSL certificate created"
else
    echo "  SSL certificate already exists"
fi

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
sudo systemctl start egmesh

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
echo "  │  3. Start the service:                              │"
echo "  │     sudo systemctl start egmesh                     │"
echo "  │                                                     │"
echo "  │  4. Open the web UI from your phone:                │"
echo "  │     https://<pi-ip>:5000                            │"
echo "  │     (accept the certificate warning)                │"
echo "  │                                                     │"
echo "  │  5. Configure radio from the web UI                 │"
echo "  │     (Radio Configuration panel)                     │"
echo "  │                                                     │"
echo "  │  6. Scan for repeaters or add manually              │"
echo "  │     (Repeater panel)                                │"
echo "  │                                                     │"
echo "  │  7. Tap Start Logging                               │"
echo "  │                                                     │"
echo "  │  GPS: Phone GPS is used automatically via browser.  │"
echo "  │  USB GPS dongle takes priority if plugged in.       │"
echo "  │                                                     │"
echo "  │  Optional: Set up Wi-Fi hotspot for field use:      │"
echo "  │     sudo ./setup_hotspot.sh                         │"
echo "  │     Then connect to EGMESH-LOGGER (pw: egmesh2025)  │"
echo "  │     Web UI: https://192.168.4.1:5000                │"
echo "  └─────────────────────────────────────────────────────┘"
echo ""
echo "  Created by Ingo Azarvand for EGMESH.NET - Elk Grove, CA"
echo ""
