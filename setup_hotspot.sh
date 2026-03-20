#!/bin/bash
# EGMESH Coverage Logger - WiFi Hotspot Setup
# Copyright (c) 2026 Ingo Azarvand / EGMESH.NET - https://egmesh.net
# Run once as: sudo bash setup_hotspot.sh
set -e
echo "  EGMESH - WiFi Hotspot Setup"
apt-get update -q
apt-get install -y hostapd dnsmasq
cat >> /etc/dhcpcd.conf << DHCP

interface wlan0
  static ip_address=192.168.4.1/24
  nohook wpa_supplicant
DHCP
cat > /etc/dnsmasq.conf << DNSMASQ
interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
DNSMASQ
cat > /etc/hostapd/hostapd.conf << HOSTAPD
interface=wlan0
driver=nl80211
ssid=EGMESH-LOGGER
hw_mode=g
channel=7
wpa=2
wpa_passphrase=egmesh2025
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
HOSTAPD
echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' >> /etc/default/hostapd
systemctl unmask hostapd
systemctl enable hostapd
systemctl enable dnsmasq
echo "  Done. WiFi: EGMESH-LOGGER / egmesh2025"
echo "  Run: sudo reboot"
