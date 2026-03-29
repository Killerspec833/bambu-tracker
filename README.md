# Bambu Filament Tracker

A companion app that connects to Bambu Lab printers via Bambu Cloud MQTT, monitors active print jobs in real time, tracks filament usage per AMS slot, maintains a local filament stock inventory, and alerts when stock is low or insufficient.

## Features

- Real-time AMS slot monitoring (material, color, remaining %) via MQTT
- Automatic filament deduction after each completed print
- Pre-print stock check alerts
- Desktop notifications via `notify-send`
- OpenClaw system event notifications
- Web UI at `http://localhost:7070` — dashboard, inventory CRUD, print history, settings
- SQLite inventory database at `~/.bambu_tracker/inventory.db`
- Supports 3 printers (P1S × 2, A1 × 1) — configurable for any number

## Requirements

- Python 3.10+
- Linux (uses `notify-send` for desktop notifications)
- `libnotify-bin` for desktop notifications: `sudo apt install libnotify-bin`
- OpenClaw CLI for system event notifications (optional)

## Setup

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure printers

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml` with your printer credentials:

```yaml
printers:
  - name: "P1S-1"
    model: P1S
    serial: "01P00A123456789"
    access_token: "ABCD1234"
    region: us
```

### 3. Get your Bambu access token

1. Open **Bambu Studio**
2. Select your printer from the device list
3. Click the gear/settings icon
4. Look for **Access Code** — this is your `access_token`
5. Your **Serial Number** is visible in the same panel and on the printer's label

**Regions:**
- `us` — North America: `us.mqtt.bambulab.com`
- `eu` — Europe: `eu.mqtt.bambulab.com`
- `ap` — Asia-Pacific: `ap.mqtt.bambulab.com`

### 4. Run

```bash
python run.py
```

The web UI will be available at [http://localhost:7070](http://localhost:7070).

## Web UI Pages

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/` | All printers, AMS slot status, active jobs |
| Inventory | `/inventory` | Spool list with remaining %, add/edit/delete |
| Print History | `/history` | Past jobs, filament used per job |
| Settings | `/settings` | Alert thresholds, printer list |

## File Structure

```
bambu-tracker/
├── bambu_tracker/
│   ├── __init__.py
│   ├── alerts.py        # notify-send + OpenClaw alerts
│   ├── config.py        # config.yaml loader/validator
│   ├── inventory.py     # SQLite CRUD (spools, jobs, events)
│   ├── models.py        # Dataclasses: Printer, AmsSlot, FilamentSpool, PrintJob
│   ├── mqtt_client.py   # Bambu Cloud MQTT client with reconnect
│   └── web_ui.py        # Flask web UI (no CDN dependencies)
├── config.yaml.example
├── requirements.txt
└── run.py               # Entry point
```

## Database

SQLite at `~/.bambu_tracker/inventory.db`. Override with env var:

```bash
BAMBU_TRACKER_DB_DIR=/path/to/dir python run.py
```

Tables: `spools`, `print_jobs`, `stock_events`

## Alerts

Two notification channels (each independently configurable):

- **Desktop** (`notify-send`): shown as system notifications
- **OpenClaw**: `openclaw system event --text "..." --mode now`

Alert types:
- Pre-print: insufficient stock for queued job
- Low stock: spool below threshold
- Print complete: usage summary
- Spool empty: needs replacement

## Notes on Bambu Cloud MQTT

Bambu Lab uses a private MQTT broker with TLS. The tracker connects using:
- Broker: `{region}.mqtt.bambulab.com:8883`
- Username: `bblp`
- Password: printer access token
- Topic: `device/{serial}/report`

Certificate verification is disabled (`CERT_NONE`) as Bambu uses a self-signed cert. Connection uses `paho-mqtt >= 2.0`.
