# -----------------------------------------------------------------------------
# EGMESH Coverage Logger
# Copyright (c) 2026 Ingo Azarvand / EGMESH.NET
# Elk Grove, California - https://egmesh.net
# Non-commercial community use permitted with attribution.
# See LICENSE for full terms.
# -----------------------------------------------------------------------------
#!/usr/bin/env python3
import asyncio, csv, os, time, threading, json, glob, subprocess
from datetime import datetime
from collections import deque
from flask import Flask, jsonify, send_file, request

# ── CONFIG ────────────────────────────────────────────────────────────────────
GPS_BAUD      = 9600
PING_INTERVAL = 30   # default — configurable from web UI
LOG_DIR       = os.path.expanduser("~/egmesh_logs")
APP_DIR       = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE   = os.path.join(APP_DIR, "repeaters.json")
# ─────────────────────────────────────────────────────────────────────────────

os.makedirs(LOG_DIR, exist_ok=True)
app = Flask(__name__)

# ── ASYNC EVENT LOOP (background thread for MeshPinger) ──────────────────────
_loop = None
_loop_thread = None

def _start_async_loop():
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_forever()

_loop_thread = threading.Thread(target=_start_async_loop, daemon=True)
_loop_thread.start()
time.sleep(0.3)

# ── MESH PINGER (persistent serial connection) ───────────────────────────────
from mesh_ping import MeshPinger
_pinger = MeshPinger()

def _run_async(coro, timeout=45):
    """Submit async coroutine to background loop, block for result."""
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=timeout)

# ── REPEATER CONFIG ───────────────────────────────────────────────────────────
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"active": None, "repeaters": []}

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def get_active_key():
    cfg = load_config()
    if not cfg["active"]:
        return None
    match = next((r for r in cfg["repeaters"] if r["id"] == cfg["active"]), None)
    return match["key"] if match else None

def get_active_name():
    cfg = load_config()
    if not cfg["active"]:
        return None
    match = next((r for r in cfg["repeaters"] if r["id"] == cfg["active"]), None)
    return match["name"] if match else None

# ── STATE ─────────────────────────────────────────────────────────────────────
state = {"running":False,"log_file":None,"ping_count":0,
         "last_ping":None,"last_gps":{"lat":None,"lon":None},"status":"Idle"}
recent_pings = deque(maxlen=100)
stop_event   = threading.Event()
ping_interval = PING_INTERVAL  # mutable at runtime

# Phone GPS state — updated by the web UI via /api/gps
phone_gps = {"lat": None, "lon": None, "updated": 0}

# ── GPS ───────────────────────────────────────────────────────────────────────
def _find_gps_port():
    """Auto-detect GPS serial port by checking USB vendor/product IDs."""
    ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    for port in ports:
        try:
            info = subprocess.run(
                f"udevadm info -q property {port}",
                shell=True, capture_output=True, text=True, timeout=5
            ).stdout.lower()
        except Exception:
            continue
        if any(tag in info for tag in ["id_vendor_id=1546", "u-blox", "ublox", "gps", "gnss"]):
            return port
    return None

def read_gps():
    """Read GPS — tries USB dongle first, falls back to phone GPS."""
    # Try USB GPS dongle first
    try:
        import serial, pynmea2
        gps_port = _find_gps_port()
        if gps_port:
            with serial.Serial(gps_port, GPS_BAUD, timeout=5) as ser:
                deadline = time.time() + 6
                while time.time() < deadline:
                    line = ser.readline().decode("ascii", errors="replace").strip()
                    if line.startswith(("$GPRMC","$GPGGA","$GNRMC","$GNGGA")):
                        try:
                            msg = pynmea2.parse(line)
                            if hasattr(msg,"latitude") and msg.latitude:
                                return round(msg.latitude,7), round(msg.longitude,7)
                        except Exception: continue
    except Exception: pass

    # Fall back to phone GPS (if received within last 30 seconds)
    if phone_gps["lat"] and (time.time() - phone_gps["updated"]) < 30:
        return phone_gps["lat"], phone_gps["lon"]

    return None, None

# ── PING ──────────────────────────────────────────────────────────────────────
def do_ping(lat=None, lon=None):
    """Ping the repeater via persistent serial connection."""
    try:
        rname = get_active_name()
        result = _run_async(_pinger.ping_repeater(lat=lat, lon=lon, repeater_name=rname))
        if result.success:
            return {
                "snr_there": result.echo_snr,
                "snr_back":  result.snr,
                "rssi":      result.rssi,
                "duration":  int((result.rtt_s or 0) * 1000),
                "noise_floor": result.noise_floor,
            }
        else:
            return {"error": result.error or "No response"}
    except Exception as e:
        return {"error": str(e)}

# ── LOGGER THREAD ─────────────────────────────────────────────────────────────
def logger_loop():
    key = get_active_key()
    if not key:
        state.update(running=False, status="No repeater selected")
        return

    cfg    = load_config()
    active = next((r for r in cfg["repeaters"] if r["id"] == cfg["active"]), {})
    rname  = active.get("name", "unknown")

    fn = os.path.join(LOG_DIR, f"coverage_{rname.replace(' ','_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    state.update(log_file=fn, ping_count=0, status="Running")
    fields = ["timestamp","latitude","longitude","snr_there_dB","snr_back_dB","duration_ms","repeater","error"]

    with open(fn,"w",newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        while not stop_event.is_set():
            ts = datetime.now().isoformat()
            state["status"] = "Reading GPS..."
            lat, lon = read_gps()
            state["last_gps"] = {"lat":lat,"lon":lon}
            state["status"] = f"Pinging {rname}..."
            ping = do_ping(lat=lat, lon=lon)
            row = {
                "timestamp":    ts,
                "latitude":     lat or "",
                "longitude":    lon or "",
                "snr_there_dB": (ping or {}).get("snr_there",""),
                "snr_back_dB":  (ping or {}).get("snr_back",""),
                "duration_ms":  (ping or {}).get("duration",""),
                "repeater":     rname,
                "error":        (ping or {}).get("error","NO_RESPONSE") if not ping or "error" in (ping or {}) else "",
            }
            w.writerow(row); f.flush()
            state["ping_count"] += 1
            state["last_ping"] = row
            recent_pings.appendleft(row)
            state["status"] = "Running"
            stop_event.wait(timeout=ping_interval)

    state.update(running=False, status="Stopped")

# ── ROUTES: CORE ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return open(os.path.join(APP_DIR, "index.html")).read()

@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": "EGMESH Coverage Logger",
        "short_name": "EGMESH",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0d1117",
        "theme_color": "#161b22",
        "icons": [
            {"src": "/static/icon.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon.png", "sizes": "512x512", "type": "image/png"}
        ]
    })

@app.route("/api/start", methods=["POST"])
def start():
    if state["running"]: return jsonify({"ok":False,"msg":"Already running"})
    if not get_active_key(): return jsonify({"ok":False,"msg":"No repeater selected"})
    stop_event.clear(); state["running"] = True
    threading.Thread(target=logger_loop, daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/stop", methods=["POST"])
def stop():
    stop_event.set(); state["running"]=False; state["status"]="Stopping..."
    return jsonify({"ok":True})

@app.route("/api/reset", methods=["POST"])
def reset():
    state.update(running=False, log_file=None, ping_count=0,
                 last_ping=None, last_gps={"lat":None,"lon":None}, status="Idle")

@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify({"ping_interval": ping_interval})

@app.route("/api/settings", methods=["POST"])
def set_settings():
    global ping_interval
    data = request.get_json(silent=True) or {}
    interval = data.get("ping_interval")
    allowed  = [5, 10, 15, 20, 30]
    if interval not in allowed:
        return jsonify({"ok": False, "error": f"Invalid interval. Must be one of {allowed}"}), 400
    ping_interval = interval
    return jsonify({"ok": True, "ping_interval": ping_interval})
    recent_pings.clear()
    return jsonify({"ok":True})

@app.route("/api/status")
def status():
    cfg = load_config()
    active = next((r for r in cfg["repeaters"] if r["id"] == cfg.get("active")), None)
    gps_source = "none"
    if _find_gps_port():
        gps_source = "usb"
    elif phone_gps["lat"] and (time.time() - phone_gps["updated"]) < 30:
        gps_source = "phone"
    return jsonify({
        **state,
        "log_file": os.path.basename(state["log_file"]) if state["log_file"] else None,
        "active_repeater": active,
        "connected": _pinger.is_connected,
        "gps_source": gps_source,
    })

# ── ROUTES: PHONE GPS ────────────────────────────────────────────────────────
@app.route("/api/gps", methods=["POST"])
def receive_phone_gps():
    """Receive GPS coordinates from phone browser."""
    data = request.get_json(silent=True) or {}
    lat = data.get("lat")
    lon = data.get("lon")
    if lat is not None and lon is not None:
        try:
            phone_gps["lat"] = round(float(lat), 7)
            phone_gps["lon"] = round(float(lon), 7)
            phone_gps["updated"] = time.time()
            return jsonify({"ok": True})
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Invalid coordinates"}), 400
    return jsonify({"ok": False, "error": "lat and lon required"}), 400

@app.route("/api/ping", methods=["POST","GET"])
def api_ping():
    lat = state["last_gps"].get("lat")
    lon = state["last_gps"].get("lon")
    return jsonify(do_ping(lat=lat, lon=lon))

@app.route("/api/pings")
def pings():
    return jsonify(list(recent_pings))

@app.route("/api/files")
def files():
    return jsonify(sorted([f for f in os.listdir(LOG_DIR) if f.endswith(".csv")], reverse=True))

@app.route("/api/download")
def dl():
    if state["log_file"] and os.path.exists(state["log_file"]):
        return send_file(state["log_file"], as_attachment=True)
    return jsonify({"error":"No log file"}), 404

@app.route("/api/download/<fn>")
def dl_file(fn):
    p = os.path.join(LOG_DIR, fn)
    return send_file(p, as_attachment=True) if os.path.exists(p) else (jsonify({"error":"Not found"}),404)

@app.route("/api/heatmap", methods=["POST"])
def gen_heatmap():
    """Generate heatmap from all CSV files or a specific one."""
    import glob as g
    data = request.get_json(silent=True) or {}
    fn = data.get("file")
    if fn:
        files = [os.path.join(LOG_DIR, fn)]
    else:
        files = sorted(g.glob(os.path.join(LOG_DIR, "*.csv")))
    if not files:
        return jsonify({"ok": False, "error": "No log files"}), 404
    try:
        import pandas as pd
        import folium
        from folium.plugins import HeatMap
        rows = []
        for f in files:
            try:
                df = pd.read_csv(f)
                rows.append(df)
            except Exception:
                continue
        if not rows:
            return jsonify({"ok": False, "error": "No valid data"}), 404
        df = pd.concat(rows, ignore_index=True)
        df = df.dropna(subset=["latitude","longitude"])
        df = df[(df["latitude"] != "") & (df["longitude"] != "")]
        df["latitude"] = df["latitude"].astype(float)
        df["longitude"] = df["longitude"].astype(float)
        if df.empty:
            return jsonify({"ok": False, "error": "No GPS data in logs"}), 404
        center = [df["latitude"].mean(), df["longitude"].mean()]
        m = folium.Map(
            location=center, zoom_start=14,
            tiles='https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
            attr='&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>'
        )
        snr_col = "snr_there_dB" if "snr_there_dB" in df.columns else None
        if snr_col:
            df[snr_col] = pd.to_numeric(df[snr_col], errors="coerce").fillna(0)
            heat = df[["latitude","longitude",snr_col]].values.tolist()
        else:
            heat = df[["latitude","longitude"]].values.tolist()
        HeatMap(heat, radius=18, blur=22).add_to(m)
        out = os.path.join(LOG_DIR, "heatmap.html")
        m.save(out)
        return send_file(out, as_attachment=True, download_name="heatmap.html")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/files/clear", methods=["POST"])
def clear_files():
    import glob as g
    current = os.path.basename(state["log_file"]) if state["log_file"] else None
    for f in g.glob(os.path.join(LOG_DIR, "*.csv")):
        if os.path.basename(f) != current:
            os.remove(f)
    return jsonify({"ok": True})

# ── ROUTES: RADIO CONFIGURATION ───────────────────────────────────────────────
@app.route("/api/radio", methods=["GET"])
def get_radio():
    try:
        info = _run_async(_pinger.get_device_info(), timeout=15)
        return jsonify({
            "ok": True,
            "freq": info.get("radio_freq"),
            "bw": info.get("radio_bw"),
            "sf": info.get("radio_sf"),
            "cr": info.get("radio_cr"),
            "tx_power": info.get("tx_power"),
            "name": info.get("name"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/radio", methods=["POST"])
def set_radio():
    data = request.get_json()
    freq = data.get("freq")
    bw = data.get("bw")
    sf = data.get("sf")
    cr = data.get("cr")
    if not all([freq, bw, sf, cr]):
        return jsonify({"ok": False, "error": "freq, bw, sf, and cr are all required"}), 400
    try:
        freq = float(freq)
        bw = float(bw)
        sf = int(sf)
        cr = int(cr)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid number format"}), 400
    try:
        result = _run_async(_pinger.configure_radio(freq, bw, sf, cr), timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ── ROUTES: CONTACT SCANNING ─────────────────────────────────────────────────
@app.route("/api/contacts/scan", methods=["GET"])
def scan_contacts():
    try:
        contacts = _run_async(_pinger.scan_contacts(), timeout=15)
        return jsonify({"ok": True, "contacts": contacts})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "contacts": []}), 500

# ── ROUTES: REPEATER MANAGEMENT ───────────────────────────────────────────────
@app.route("/api/repeaters")
def get_repeaters():
    return jsonify(load_config())

@app.route("/api/repeaters/add", methods=["POST"])
def add_repeater():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    key  = (data.get("key")  or "").strip()
    if not name or not key:
        return jsonify({"ok":False,"msg":"Name and key are required"}), 400
    cfg = load_config()
    if any(r["key"] == key for r in cfg["repeaters"]):
        return jsonify({"ok":False,"msg":"That key already exists"}), 400
    new_id = str(int(time.time()))
    cfg["repeaters"].append({"id": new_id, "name": name, "key": key})
    if not cfg["active"]:
        cfg["active"] = new_id
    save_config(cfg)
    try:
        _run_async(_pinger.add_contact_to_device(name, key, contact_type=2), timeout=10)
    except Exception:
        pass
    return jsonify({"ok":True,"id":new_id})

@app.route("/api/repeaters/select", methods=["POST"])
def select_repeater():
    data = request.get_json()
    rid  = data.get("id")
    cfg  = load_config()
    if not any(r["id"] == rid for r in cfg["repeaters"]):
        return jsonify({"ok":False,"msg":"Repeater not found"}), 404
    cfg["active"] = rid
    save_config(cfg)
    return jsonify({"ok":True})

@app.route("/api/repeaters/delete", methods=["POST"])
def delete_repeater():
    data = request.get_json()
    rid  = data.get("id")
    cfg  = load_config()
    cfg["repeaters"] = [r for r in cfg["repeaters"] if r["id"] != rid]
    if cfg["active"] == rid:
        cfg["active"] = cfg["repeaters"][0]["id"] if cfg["repeaters"] else None
    save_config(cfg)
    return jsonify({"ok":True})

if __name__ == "__main__":
    from werkzeug.serving import make_server
    import ssl

    print("EGMESH Logger → https://0.0.0.0:5000  (phone/GPS — HTTPS)")
    print("EGMESH Logger → http://0.0.0.0:5001   (desktop — HTTP)")

    # ── HTTPS server on :5000 (required for phone geolocation) ───────────────
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain('cert.pem', 'key.pem')
    https_server = make_server('0.0.0.0', 5000, app, ssl_context=ssl_ctx)

    # ── HTTP server on :5001 (desktop — no cert warning) ─────────────────────
    http_server = make_server('0.0.0.0', 5001, app)

    https_thread = threading.Thread(target=https_server.serve_forever, daemon=True)
    http_thread  = threading.Thread(target=http_server.serve_forever,  daemon=True)

    https_thread.start()
    http_thread.start()

    https_thread.join()
    http_thread.join()
