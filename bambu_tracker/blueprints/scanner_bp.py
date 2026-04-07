from __future__ import annotations

"""
Companion scanner blueprint.

GET  /scan          → phone-optimised full-screen scanner PWA
POST /api/scan      → JSON: resolve barcode, optionally load/unload spool
GET  /manifest.json → PWA manifest
"""

import json
from html import escape as h

from flask import Blueprint, Response, g, jsonify, request
from flask_login import current_user, login_required

scanner_bp = Blueprint("scanner", __name__)

_ZXING_CDN = "/static/zxing-browser.min.js"

# ─── PWA manifest ─────────────────────────────────────────────────────────────

@scanner_bp.route("/manifest.json")
def pwa_manifest():
    manifest = {
        "name": "Bambu Tracker Scanner",
        "short_name": "BT Scanner",
        "start_url": "/scan",
        "display": "fullscreen",
        "background_color": "#1e1b4b",
        "theme_color": "#4f46e5",
        "icons": [],
    }
    return Response(json.dumps(manifest), mimetype="application/json")


# ─── scanner page ─────────────────────────────────────────────────────────────

@scanner_bp.route("/scan")
@login_required
def scanner_page():
    inv = g.inv
    all_printers = inv.list_printers()
    printer_opts = "".join(
        f'<option value="{p["id"]}">{h(p["name"])}</option>'
        for p in all_printers
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#4f46e5">
<link rel="manifest" href="/manifest.json">
<title>Bambu Scanner</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ height: 100%; background: #0f0f1a; color: #f1f5f9; font-family: system-ui, sans-serif; }}
#app {{ display: flex; flex-direction: column; height: 100%; }}

/* ── header ── */
#topbar {{ background: #1e1b4b; padding: 12px 16px; display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-shrink: 0; }}
#topbar h1 {{ font-size: 1rem; }}
#topbar a {{ color: #c7d2fe; font-size: .82rem; }}

/* ── camera viewport ── */
#viewfinder-wrap {{ position: relative; flex: 1; overflow: hidden; background: #000; display: flex; align-items: center; justify-content: center; }}
#video {{ width: 100%; height: 100%; object-fit: cover; }}
#scan-overlay {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; pointer-events: none; }}
#scan-box {{ width: 240px; height: 120px; border: 3px solid #4f46e5; border-radius: 10px; box-shadow: 0 0 0 9999px rgba(0,0,0,.45); animation: pulse 2s infinite; }}
@keyframes pulse {{ 0%,100% {{ border-color: #4f46e5; }} 50% {{ border-color: #818cf8; }} }}
#scan-status {{ position: absolute; bottom: 12px; left: 50%; transform: translateX(-50%);
               background: rgba(0,0,0,.7); color: #c7d2fe; font-size: .82rem;
               padding: 6px 16px; border-radius: 999px; white-space: nowrap; }}

/* ── action panel ── */
#panel {{ background: #1e1b4b; padding: 16px; flex-shrink: 0; max-height: 55vh; overflow-y: auto; }}
#spool-card {{ display: none; }}
#spool-card.show {{ display: block; }}
.spool-info {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }}
.spool-dot {{ width: 40px; height: 40px; border-radius: 50%; border: 2px solid #334155; flex-shrink: 0; }}
.spool-name {{ font-weight: 600; font-size: 1rem; }}
.spool-meta {{ font-size: .82rem; color: #94a3b8; margin-top: 2px; }}
.pct-bar {{ height: 8px; background: #334155; border-radius: 4px; overflow: hidden; margin: 8px 0; }}
.pct-fill {{ height: 100%; border-radius: 4px; background: #4f46e5; }}
.pct-low {{ background: #ef4444; }}
.pct-mid {{ background: #f59e0b; }}

label {{ font-size: .82rem; font-weight: 600; color: #94a3b8; display: block; margin-top: 12px; }}
select {{ width: 100%; padding: 8px 12px; margin-top: 4px; border: 1px solid #334155; border-radius: 6px; background: #0f172a; color: #f1f5f9; font-size: .9rem; }}
.btn-row {{ display: flex; gap: 10px; margin-top: 14px; }}
.btn {{ flex: 1; padding: 12px; border-radius: 8px; font-size: .92rem; font-weight: 600;
        cursor: pointer; border: none; text-align: center; }}
.btn-load {{ background: #4f46e5; color: #fff; }}
.btn-unload {{ background: #dc2626; color: #fff; }}
.btn-scan {{ background: #334155; color: #e2e8f0; }}
.btn:disabled {{ opacity: .5; cursor: not-allowed; }}

#msg-area {{ margin-top: 12px; padding: 10px 14px; border-radius: 6px; font-size: .85rem; display: none; }}
.msg-ok  {{ background: #14532d; color: #bbf7d0; }}
.msg-err {{ background: #7f1d1d; color: #fecaca; }}
.msg-warn {{ background: #78350f; color: #fef3c7; }}

#start-prompt {{ text-align: center; padding: 24px 16px; }}
#start-prompt p {{ color: #94a3b8; font-size: .88rem; margin-bottom: 16px; }}
#btn-start-camera {{ background: #4f46e5; color: #fff; font-size: 1rem; font-weight: 600;
                    padding: 14px 32px; border-radius: 8px; border: none; cursor: pointer; }}
</style>
</head>
<body>
<div id="app">
  <div id="topbar">
    <h1>Bambu Scanner</h1>
    <a href="/">← Dashboard</a>
  </div>

  <div id="viewfinder-wrap">
    <video id="video" playsinline autoplay muted></video>
    <div id="scan-overlay"><div id="scan-box"></div></div>
    <div id="scan-status">Initialising camera…</div>
  </div>

  <div id="panel">
    <div id="start-prompt">
      <p>Point the camera at a barcode or QR code on a spool label.</p>
      <button id="btn-start-camera">Start Camera</button>
    </div>

    <div id="spool-card">
      <div class="spool-info">
        <div class="spool-dot" id="sc-dot"></div>
        <div>
          <div class="spool-name" id="sc-name"></div>
          <div class="spool-meta" id="sc-meta"></div>
        </div>
      </div>
      <div class="pct-bar"><div class="pct-fill" id="sc-pct-fill"></div></div>
      <div id="sc-pct-label" style="font-size:.8rem;color:#94a3b8"></div>

      <label>Target Printer
        <select id="sel-printer">{printer_opts}</select>
      </label>
      <label>AMS Slot
        <select id="sel-slot">
          <option value="0">Slot 0</option>
          <option value="1">Slot 1</option>
          <option value="2">Slot 2</option>
          <option value="3">Slot 3</option>
        </select>
      </label>

      <div class="btn-row">
        <button class="btn btn-load" id="btn-load">Load</button>
        <button class="btn btn-unload" id="btn-unload">Unload</button>
        <button class="btn btn-scan" id="btn-rescan">Scan Again</button>
      </div>
      <div id="msg-area"></div>
    </div>
  </div>
</div>

<script src="{_ZXING_CDN}"></script>
<script>
const video  = document.getElementById('video');
const status = document.getElementById('scan-status');
const card   = document.getElementById('spool-card');
const prompt = document.getElementById('start-prompt');
const msgArea = document.getElementById('msg-area');

let scanning = false;
let codeReader = null;
let lastBarcode = null;
let beepCtx = null;

// ── tiny beep ──
function beep(ok) {{
  try {{
    if (!beepCtx) beepCtx = new AudioContext();
    const o = beepCtx.createOscillator();
    const g = beepCtx.createGain();
    o.connect(g); g.connect(beepCtx.destination);
    o.frequency.value = ok ? 880 : 330;
    g.gain.setValueAtTime(0.3, beepCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, beepCtx.currentTime + 0.2);
    o.start(); o.stop(beepCtx.currentTime + 0.2);
  }} catch(_) {{}}
}}

function showMsg(text, kind='ok') {{
  msgArea.textContent = text;
  msgArea.className = 'msg-' + kind;
  msgArea.style.display = 'block';
  if (kind === 'ok') setTimeout(() => msgArea.style.display='none', 3000);
}}

function pctClass(pct) {{
  return pct <= 15 ? 'pct-low' : pct <= 35 ? 'pct-mid' : '';
}}

async function postScan(payload) {{
  const r = await fetch('/api/scan', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(payload),
  }});
  return r.json();
}}

async function onBarcode(code) {{
  if (!scanning) return;
  scanning = false;
  status.textContent = 'Resolving…';
  lastBarcode = code;
  try {{
    const d = await postScan({{ barcode_id: code, action: 'lookup' }});
    if (!d.spool) {{
      beep(false);
      showMsg('Spool not found: ' + code, 'err');
      setTimeout(startScan, 2000);
      return;
    }}
    beep(true);
    // populate card
    const s = d.spool;
    const pct = s.total_weight_g > 0 ? Math.round(s.remaining_g / s.total_weight_g * 100) : 0;
    document.getElementById('sc-dot').style.background = s.color_hex || '#888';
    document.getElementById('sc-name').textContent = s.name;
    document.getElementById('sc-meta').textContent =
      s.material + ' · ' + (s.brand || 'Unknown') + ' · ' + s.barcode_id;
    const fill = document.getElementById('sc-pct-fill');
    fill.style.width = pct + '%';
    fill.className = 'pct-fill ' + pctClass(pct);
    document.getElementById('sc-pct-label').textContent =
      s.remaining_g.toFixed(0) + 'g remaining (' + pct + '%)';
    prompt.style.display = 'none';
    card.classList.add('show');
    status.textContent = 'Scan another or take action';
    if (d.current_location) {{
      showMsg('Currently loaded: ' + d.current_location.printer_name + ' / S' + d.current_location.ams_slot, 'warn');
    }}
  }} catch(e) {{
    showMsg('Network error: ' + e, 'err');
    setTimeout(startScan, 2000);
  }}
}}

function startScan() {{
  scanning = true;
  status.textContent = 'Scanning…';
  // Try native BarcodeDetector first
  if ('BarcodeDetector' in window) {{
    const bd = new BarcodeDetector({{ formats: ['code_128', 'qr_code', 'code_39', 'ean_13'] }});
    function detect() {{
      if (!scanning) return;
      bd.detect(video).then(codes => {{
        if (codes.length > 0) {{ onBarcode(codes[0].rawValue); return; }}
      }}).catch(()=>{{}}).finally(() => {{ if (scanning) requestAnimationFrame(detect); }});
    }}
    requestAnimationFrame(detect);
  }} else if (typeof ZXingBrowser !== 'undefined') {{
    // ZXing fallback
    if (!codeReader) codeReader = new ZXingBrowser.BrowserMultiFormatReader();
    codeReader.decodeOnceFromVideoElement(video)
      .then(result => {{ if (result) onBarcode(result.getText()); }})
      .catch(() => {{ if (scanning) setTimeout(startScan, 500); }});
  }} else {{
    status.textContent = 'No scanner available in this browser';
  }}
}}

async function startCamera() {{
  try {{
    const stream = await navigator.mediaDevices.getUserMedia({{
      video: {{ facingMode: {{ ideal: 'environment' }}, width: {{ ideal: 1280 }} }}
    }});
    video.srcObject = stream;
    await video.play();
    prompt.style.display = 'none';
    status.textContent = 'Scanning…';
    startScan();
  }} catch(e) {{
    status.textContent = 'Camera denied: ' + e.message;
  }}
}}

document.getElementById('btn-start-camera').onclick = startCamera;

document.getElementById('btn-rescan').onclick = () => {{
  card.classList.remove('show');
  prompt.style.display = 'block';
  msgArea.style.display = 'none';
  lastBarcode = null;
  startScan();
}};

document.getElementById('btn-load').onclick = async () => {{
  if (!lastBarcode) return;
  const printer_id = document.getElementById('sel-printer').value;
  const ams_slot = document.getElementById('sel-slot').value;
  const d = await postScan({{ barcode_id: lastBarcode, action: 'load', printer_id: parseInt(printer_id), ams_slot: parseInt(ams_slot) }});
  if (d.result === 'ok') {{ beep(true); showMsg('Loaded successfully!', 'ok'); }}
  else if (d.result === 'already_loaded') {{ beep(false); showMsg('Spool is already loaded elsewhere.', 'warn'); }}
  else if (d.result === 'conflict') {{ beep(false); showMsg('Slot is occupied by another spool.', 'err'); }}
  else {{ beep(false); showMsg('Error: ' + (d.error || d.result), 'err'); }}
}};

document.getElementById('btn-unload').onclick = async () => {{
  if (!lastBarcode) return;
  const d = await postScan({{ barcode_id: lastBarcode, action: 'unload' }});
  if (d.result === 'ok') {{ beep(true); showMsg('Unloaded.', 'ok'); }}
  else {{ beep(false); showMsg('Not currently loaded or error.', 'warn'); }}
}};

// Auto-start on page load if permissions already granted
navigator.permissions && navigator.permissions.query({{name:'camera'}}).then(p => {{
  if (p.state === 'granted') startCamera();
}}).catch(()=>{{}});
</script>
</body>
</html>"""

    return Response(html, mimetype="text/html")


# ─── QR deep-link redirect ────────────────────────────────────────────────────

@scanner_bp.route("/scan/<barcode_id>")
@login_required
def scan_redirect(barcode_id: str):
    """QR code payload target: redirect phone camera to the spool detail page."""
    from flask import redirect, url_for
    inv = g.inv
    spool = inv.get_spool_by_barcode(barcode_id.upper())
    if not spool:
        return f"Spool {barcode_id!r} not found.", 404
    return redirect(url_for("inventory.spool_detail", spool_id=spool["id"]))


# ─── JSON scan API ────────────────────────────────────────────────────────────

@scanner_bp.route("/api/scan", methods=["POST"])
@login_required
def api_scan():
    inv = g.inv
    data = request.get_json(silent=True) or {}
    barcode_id = (data.get("barcode_id") or "").strip().upper()
    action = data.get("action", "lookup")
    ua = request.headers.get("User-Agent", "")
    ip = request.remote_addr or ""

    if not barcode_id:
        return jsonify({"error": "barcode_id is required"}), 400

    spool = inv.get_spool_by_barcode(barcode_id)
    if not spool:
        inv.record_scan_event(barcode_id, action, "not_found",
                              scanned_by=current_user.id, user_agent=ua, ip_address=ip)
        return jsonify({"result": "not_found", "spool": None})

    location = inv.get_active_location(spool["id"])

    if action == "lookup":
        inv.record_scan_event(barcode_id, "lookup", "ok",
                              scanned_by=current_user.id, user_agent=ua, ip_address=ip)
        return jsonify({"result": "ok", "spool": _spool_json(spool), "current_location": location})

    if action == "load":
        printer_id = data.get("printer_id")
        ams_slot = data.get("ams_slot", 0)
        if not printer_id:
            return jsonify({"error": "printer_id required for load"}), 400
        result = inv.load_spool(spool["id"], int(printer_id), int(ams_slot), user_id=current_user.id)
        inv.record_scan_event(barcode_id, "load", result,
                              scanned_by=current_user.id, printer_id=int(printer_id),
                              ams_slot=int(ams_slot), user_agent=ua, ip_address=ip)
        return jsonify({"result": result, "spool": _spool_json(spool)})

    if action == "unload":
        ok = inv.unload_spool(spool["id"], user_id=current_user.id)
        result = "ok" if ok else "not_loaded"
        inv.record_scan_event(barcode_id, "unload", result,
                              scanned_by=current_user.id, user_agent=ua, ip_address=ip)
        return jsonify({"result": result, "spool": _spool_json(spool)})

    return jsonify({"error": "unknown action"}), 400


def _spool_json(s: dict) -> dict:
    return {
        "id": s["id"],
        "barcode_id": s.get("barcode_id"),
        "name": s["name"],
        "material": s["material"],
        "brand": s.get("brand") or "",
        "color_hex": s.get("color_hex") or "#aaa",
        "total_weight_g": float(s.get("total_weight_g") or 0),
        "remaining_g": float(s.get("remaining_g") or 0),
        "low_stock_threshold_g": float(s.get("low_stock_threshold_g") or 50),
    }
