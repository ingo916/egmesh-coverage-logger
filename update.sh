#!/bin/bash
# EGMESH Coverage Logger - Update Script
# Copyright (c) 2026 Ingo Azarvand / EGMESH.NET - https://egmesh.net
#
# Copies repo files to the runtime directory and restarts services.
# Run from the repo directory after making changes:
#   sudo bash update.sh

INSTALL_DIR="/root/egmesh_logger"

echo ""
echo "  EGMESH Coverage Logger - Update"
echo ""

if [ ! -d "$INSTALL_DIR" ]; then
    echo "  ERROR: $INSTALL_DIR not found — run install.sh first"
    exit 1
fi

echo "  Copying files..."
cp app.py         "$INSTALL_DIR/app.py"
cp mesh_ping.py   "$INSTALL_DIR/mesh_ping.py"
cp index.html     "$INSTALL_DIR/index.html"
cp wifi_toggle.py "$INSTALL_DIR/wifi_toggle.py"
[ -f heatmap.py ] && cp heatmap.py "$INSTALL_DIR/heatmap.py"

echo "  Restarting services..."
systemctl restart egmesh
systemctl restart egmesh-wifi

echo ""
echo "  Done. Services restarted."
echo ""

