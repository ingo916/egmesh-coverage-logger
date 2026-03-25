# ─────────────────────────────────────────────────────────────────────────────
# EGMESH Coverage Logger — Heatmap Generator
# Copyright (c) 2026 Ingo Azarvand / EGMESH.NET
# Elk Grove, California — https://egmesh.net
#
# Reads a coverage CSV log and generates an interactive HTML heatmap
# overlaid on an OpenStreetMap street map. No API key required.
# Internet connection required when viewing the output HTML file.
#
# EGMESH.NET Radio Settings:
#   Frequency : 910.525 MHz
#   Bandwidth : 125 kHz
#   SF        : 9
#   CR        : 4/5
#   TX Power  : 20 dBm
#
# Usage:
#   python heatmap.py                          # auto-finds newest CSV
#   python heatmap.py coverage_log_XYZ.csv    # specific file
#   python heatmap.py *.csv                   # merge multiple runs
# ─────────────────────────────────────────────────────────────────────────────

import pandas as pd
import folium
from folium.plugins import HeatMap
import sys
import os
import glob
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────────────────────
SNR_MIN      = -20.0   # dB floor — treated as zero signal
SNR_MAX      =  15.0   # dB ceiling — treated as full signal
OUTPUT_DIR   = os.path.expanduser("~/egmesh_logs")
MAP_TILES    = "OpenStreetMap"   # free, no API key needed
DEFAULT_ZOOM = 13
# ─────────────────────────────────────────────────────────────────────────────


def snr_to_weight(val):
    """Normalize a SNR value to 0.0–1.0 for heatmap intensity."""
    try:
        snr = float(val)
        return max(0.0, min(1.0, (snr - SNR_MIN) / (SNR_MAX - SNR_MIN)))
    except (ValueError, TypeError):
        return 0.0


def load_csv(path):
    """Load and validate a coverage CSV file."""
    df = pd.read_csv(path)
    required = ["latitude", "longitude"]
    for col in required:
        if col not in df.columns:
            print(f"  [SKIP] {os.path.basename(path)} — missing column: {col}")
            return None
    df = df.dropna(subset=["latitude", "longitude"])
    df = df[df["latitude"] != ""]
    df["latitude"]  = pd.to_numeric(df["latitude"],  errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"])
    return df


def snr_color(val):
    """Return a hex color for a marker based on SNR value."""
    try:
        n = float(val)
        if n >= 5:   return "#3fb950"   # green  — strong
        if n >= 0:   return "#d29922"   # yellow — ok
        if n >= -5:  return "#f0883e"   # orange — weak
        return "#f85149"                # red    — marginal
    except (ValueError, TypeError):
        return "#484f58"                # gray   — no response


def generate_heatmap(csv_files, output_path):
    """Load one or more CSVs and generate a combined heatmap."""
    frames = []
    for f in csv_files:
        df = load_csv(f)
        if df is not None:
            df["_source"] = os.path.basename(f)
            frames.append(df)
            print(f"  [LOAD] {os.path.basename(f)} — {len(df)} points")

    if not frames:
        print("  [ERROR] No valid data found.")
        return

    data = pd.concat(frames, ignore_index=True)
    total = len(data)
    responded = data["snr_back_dB"].apply(
        lambda x: str(x) not in ["", "NO_RESPONSE", "nan"]
    ).sum()

    print(f"\n  Total data points : {total}")
    print(f"  Ping responses    : {responded}")
    print(f"  No response       : {total - responded}")

    # Center map on data centroid
    center_lat = data["latitude"].mean()
    center_lon = data["longitude"].mean()

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=DEFAULT_ZOOM,
        tiles=MAP_TILES
    )

    # ── HEATMAP LAYER ─────────────────────────────────────────────────────────
    heat_data = []
    for _, row in data.iterrows():
        weight = snr_to_weight(row.get("snr_back_dB", ""))
        if weight > 0:
            heat_data.append([row["latitude"], row["longitude"], weight])

    if heat_data:
        HeatMap(
            heat_data,
            min_opacity=0.3,
            max_zoom=18,
            radius=25,
            blur=20,
            gradient={
                "0.0":  "#0000ff",   # blue   — marginal
                "0.25": "#00ffff",   # cyan
                "0.5":  "#00ff00",   # green  — ok
                "0.75": "#ffff00",   # yellow — good
                "1.0":  "#ff0000",   # red    — strong
            }
        ).add_to(m)

    # ── INDIVIDUAL PING MARKERS ───────────────────────────────────────────────
    marker_group = folium.FeatureGroup(name="Ping points", show=True)

    for _, row in data.iterrows():
        snr_b  = row.get("snr_back_dB",  "")
        snr_t  = row.get("snr_there_dB", "")
        dur    = row.get("duration_ms",  "")
        ts     = row.get("timestamp",    "")
        rptr   = row.get("repeater",     "")
        source = row.get("_source",      "")

        no_resp = str(snr_b) in ["", "NO_RESPONSE", "nan"]
        color   = snr_color(snr_b) if not no_resp else "#484f58"

        popup_html = f"""
            <div style="font-family:monospace;font-size:12px;min-width:200px">
              <b style="font-size:13px">{ts[:19] if ts else '—'}</b><br>
              <hr style="margin:4px 0;border-color:#ccc">
              SNR back (↓):  <b style="color:{color}">{snr_b if not no_resp else 'NO RESPONSE'}</b> dB<br>
              SNR there (↑): <b>{snr_t}</b> dB<br>
              Round trip:    <b>{dur}</b> ms<br>
              <hr style="margin:4px 0;border-color:#ccc">
              Repeater: {rptr}<br>
              <span style="color:#999;font-size:10px">{source}</span>
            </div>
        """

        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=5,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.8,
            weight=1,
            popup=folium.Popup(popup_html, max_width=250)
        ).add_to(marker_group)

    marker_group.add_to(m)

    # ── LEGEND ────────────────────────────────────────────────────────────────
    legend_html = """
    <div style="
        position: fixed;
        bottom: 30px; right: 15px;
        background: rgba(13,17,23,0.92);
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 12px 16px;
        font-family: monospace;
        font-size: 12px;
        color: #e6edf3;
        z-index: 9999;
        min-width: 160px;
    ">
      <div style="font-weight:700;margin-bottom:8px;color:#58a6ff">⬡ EGMESH Signal</div>
      <div><span style="color:#3fb950">●</span> &nbsp;Strong  (SNR ≥ 5 dB)</div>
      <div><span style="color:#d29922">●</span> &nbsp;Good    (0 to 5 dB)</div>
      <div><span style="color:#f0883e">●</span> &nbsp;Weak    (-5 to 0 dB)</div>
      <div><span style="color:#f85149">●</span> &nbsp;Marginal (&lt; -5 dB)</div>
      <div><span style="color:#484f58">●</span> &nbsp;No response</div>
      <hr style="border-color:#30363d;margin:8px 0">
      <div style="color:#8b949e;font-size:10px">
        910.525 MHz · SF10 · BW125 · CR4/5 · 22 dBm<br>
        egmesh.net · Elk Grove, CA
      </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # ── LAYER CONTROL ─────────────────────────────────────────────────────────
    folium.LayerControl().add_to(m)

    # ── SUMMARY TITLE ─────────────────────────────────────────────────────────
    title_html = f"""
    <div style="
        position: fixed;
        top: 15px; left: 50%;
        transform: translateX(-50%);
        background: rgba(13,17,23,0.92);
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 8px 20px;
        font-family: monospace;
        font-size: 13px;
        color: #e6edf3;
        z-index: 9999;
        white-space: nowrap;
    ">
      ⬡ EGMESH Coverage Map &nbsp;·&nbsp;
      {responded}/{total} responses &nbsp;·&nbsp;
      {datetime.now().strftime('%Y-%m-%d')}
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    # ── SAVE ──────────────────────────────────────────────────────────────────
    m.save(output_path)
    print(f"\n  ✓ Heatmap saved → {output_path}")
    print(f"  Open in any browser to view your coverage map.")
    print(f"  (Internet required for OpenStreetMap tiles)\n")


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n  ⬡ EGMESH Heatmap Generator")
    print(f"  Copyright (c) 2026 Ingo Azarvand / EGMESH.NET\n")

    if len(sys.argv) >= 2:
        # Files passed as arguments — support wildcards
        csv_files = []
        for arg in sys.argv[1:]:
            csv_files.extend(glob.glob(arg))
        if not csv_files:
            print(f"  [ERROR] No files found matching: {sys.argv[1:]}")
            sys.exit(1)
    else:
        # Auto-find the most recent CSV in log directory
        pattern = os.path.join(OUTPUT_DIR, "coverage_*.csv")
        csv_files = sorted(glob.glob(pattern), reverse=True)
        if not csv_files:
            print(f"  [ERROR] No coverage CSV files found in {OUTPUT_DIR}")
            print(f"  Usage: python heatmap.py coverage_log.csv")
            sys.exit(1)
        # Default: use most recent file
        csv_files = [csv_files[0]]
        print(f"  Auto-selected: {os.path.basename(csv_files[0])}\n")

    # Output filename based on input
    base = os.path.splitext(os.path.basename(csv_files[0]))[0]
    output = os.path.join(OUTPUT_DIR, f"heatmap_{base}.html")

    generate_heatmap(csv_files, output)
