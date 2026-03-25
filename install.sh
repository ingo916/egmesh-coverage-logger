#!/bin/bash
# EGMESH Coverage Logger - Installer v3.1
# Copyright (c) 2026 Ingo Azarvand / EGMESH.NET - https://egmesh.net
# Run from the repo directory: bash install.sh
set -e

echo ""
echo "  EGMESH Coverage Logger - Installer v3.1"
echo "  https://egmesh.net"
echo ""

INSTALL_DIR="/root/egmesh_logger"
LOG_DIR="/root/egmesh_logs"

# ── Create directories ────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR" "$LOG_DIR"

# ── Copy application files ────────────────────────────────────────────────────
echo "  Copying application files..."
cp app.py         "$INSTALL_DIR/app.py"
cp mesh_ping.py   "$INSTALL_DIR/mesh_ping.py"
cp index.html     "$INSTALL_DIR/index.html"
cp wifi_toggle.py "$INSTALL_DIR/wifi_toggle.py"
[ -f heatmap.py ] && cp heatmap.py "$INSTALL_DIR/heatmap.py"

# ── Initialize repeaters.json if not present ─────────────────────────────────
if [ ! -f "$INSTALL_DIR/repeaters.json" ]; then
    echo '{"active": null, "repeaters": []}' > "$INSTALL_DIR/repeaters.json"
fi

# ── Python virtual environment ────────────────────────────────────────────────
echo "  Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
echo "  Installing Python dependencies..."
"$INSTALL_DIR/venv/bin/pip" install \
    flask meshcore meshcore-cli pynmea2 pyserial pyopenssl folium pandas --quiet

# ── gpiozero (required for WiFi toggle button) ────────────────────────────────
echo "  Checking gpiozero..."
if ! python3 -c "import gpiozero" 2>/dev/null; then
    echo "  Installing gpiozero..."
    sudo apt-get install -y -q python3-gpiozero
fi

# ── SSL certificate (required for phone GPS) ─────────────────────────────────
if [ ! -f "$INSTALL_DIR/cert.pem" ]; then
    echo "  Generating SSL certificate for phone GPS..."
    openssl req -x509 -newkey rsa:2048 \
        -keyout "$INSTALL_DIR/key.pem" \
        -out    "$INSTALL_DIR/cert.pem" \
        -days 3650 -nodes \
        -subj "/CN=EGMESH" 2>/dev/null
    echo "  SSL certificate created"
else
    echo "  SSL certificate already exists — skipping"
fi

# ── egmesh.service (logger — HTTPS:5000 + HTTP:5001) ─────────────────────────
echo "  Installing egmesh.service..."
sudo bash -c "cat > /etc/systemd/system/egmesh.service << SVCEOF
[Unit]
Description=EGMESH Coverage Logger
After=network.target

[Service]
Type=simple
User=root
Environment=HOME=/root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF"

sudo systemctl daemon-reload
sudo systemctl enable egmesh
sudo systemctl restart egmesh

# ── egmesh-wifi.service (GPIO 17 WiFi toggle button) ─────────────────────────
echo "  Installing egmesh-wifi.service..."
sudo bash -c "cat > /etc/systemd/system/egmesh-wifi.service << WIFIEOF
[Unit]
Description=EGMESH WiFi Toggle Button
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 $INSTALL_DIR/wifi_toggle.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
WIFIEOF"

sudo systemctl daemon-reload
sudo systemctl enable egmesh-wifi
sudo systemctl restart egmesh-wifi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "  ┌─────────────────────────────────────────────────────┐"
echo "  │  Install complete!                                  │"
echo "  │                                                     │"
echo "  │  1. Flash USB Companion firmware on your radio:     │"
echo "  │     https://flasher.meshcore.co                     │"
echo "  │                                                     │"
echo "  │  2. Plug the radio into the Pi via USB              │"
echo "  │                                                     │"
echo "  │  3. Open the web UI:                                │"
echo "  │     Phone  : https://<pi-ip>:5000                   │"
echo "  │     Desktop: http://<pi-ip>:5001                    │"
echo "  │                                                     │"
echo "  │  4. Configure radio → add repeater → Start Logging  │"
echo "  │                                                     │"
echo "  │  FIELD USE:                                         │"
echo "  │  Press GPIO 17 once  → hotspot EGMESH-LOGGER        │"
echo "  │  Connect phone → browser opens logger automatically │"
echo "  │  Web UI: https://10.42.0.1:5000                     │"
echo "  │  Double-press GPIO 17 → back to home WiFi           │"
echo "  └─────────────────────────────────────────────────────┘"
echo ""
echo "  Created by Ingo Azarvand for EGMESH.NET - Elk Grove, CA"
echo ""
