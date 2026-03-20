#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# EGMESH Coverage Logger
# Copyright (c) 2026 Ingo Azarvand / EGMESH.NET
# Elk Grove, California - https://egmesh.net
# Non-commercial community use permitted with attribution.
# See LICENSE for full terms.
# -----------------------------------------------------------------------------
# Copyright (c) 2026 Ingo Azarvand / EGMESH.NET
# Elk Grove, California - https://egmesh.net
# Non-commercial community use permitted with attribution.
# See LICENSE for full terms.
# -----------------------------------------------------------------------------
#!/usr/bin/env python3
import asyncio, csv, os, time, threading, json
from datetime import datetime
from collections import deque
from flask import Flask, jsonify, send_file, request

# ── CONFIG ────────────────────────────────────────────────────────────────────
GPS_PORT      = "/dev/ttyUSB0"
GPS_BAUD      = 9600
PING_INTERVAL = 30
LORA_FREQ     = 910.525
LOG_DIR       = os.path.expanduser("~/egmesh_logs")
CONFIG_FILE   = os.path.expanduser("~/egmesh_logger/repeaters.json")
# ─────────────────────────────────────────────────────────────────────────────

os.makedirs(LOG_DIR, exist_ok=True)
app = Flask(__name__)

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

# ── STATE ─────────────────────────────────────────────────────────────────────
state = {"running":False,"log_file":None,"ping_count":0,
         "last_ping":None,"last_gps":{"lat":None,"lon":None},"status":"Idle"}
recent_pings = deque(maxlen=100)
stop_event   = threading.Event()

# ── GPS ───────────────────────────────────────────────────────────────────────
def read_gps():
    try:
        import serial, pynmea2
        with serial.Serial(GPS_PORT, GPS_BAUD, timeout=5) as ser:
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
    return None, None

# ── PING ──────────────────────────────────────────────────────────────────────
def do_ping(key):
    try:
        from pymc_core import MeshNode, LocalIdentity
        from pymc_core.radio.waveshare import WaveshareRadio
        result = {}
        async def _ping():
            node = MeshNode(identity=LocalIdentity.generate(),
                            radio=WaveshareRadio(frequency=LORA_FREQ))
            await node.start()
            def cb(p):
                result["snr_there"] = getattr(p,"snr_there",None)
                result["snr_back"]  = getattr(p,"snr_rx",None)
                result["duration"]  = getattr(p,"duration_ms",None)
            t0 = time.time()
            await node.ping(key, callback=cb, timeout=15)
            if "duration" not in result:
                result["duration"] = round((time.time()-t0)*1000)
            await node.stop()
        asyncio.run(_ping())
        return result if result else None
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
            ping = do_ping(key)
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
            stop_event.wait(timeout=PING_INTERVAL)

    state.update(running=False, status="Stopped")

# ── ROUTES: CORE ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return open(os.path.join(os.path.dirname(__file__),"index.html")).read()

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

@app.route("/api/status")
def status():
    cfg = load_config()
    active = next((r for r in cfg["repeaters"] if r["id"] == cfg.get("active")), None)
    return jsonify({
        **state,
        "log_file": os.path.basename(state["log_file"]) if state["log_file"] else None,
        "active_repeater": active,
    })

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
    # Check for duplicate key
    if any(r["key"] == key for r in cfg["repeaters"]):
        return jsonify({"ok":False,"msg":"That key already exists"}), 400
    new_id = str(int(time.time()))
    cfg["repeaters"].append({"id": new_id, "name": name, "key": key})
    # Auto-select if it's the first one
    if not cfg["active"]:
        cfg["active"] = new_id
    save_config(cfg)
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
    print("EGMESH Logger → http://192.168.4.1:5000")
    app.run(host="0.0.0.0", port=5000, threaded=True)
