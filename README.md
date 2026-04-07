# Bambu Filament Tracker

A companion app for Bambu Lab printers. Connects to Bambu Cloud MQTT, monitors active print jobs in real time, tracks filament usage per AMS slot, maintains a spool inventory, and alerts when stock is low or insufficient.

## Features

- Real-time AMS slot monitoring (material, color, remaining %) via MQTT
- Automatic filament deduction after each completed print
- Pre-print stock check alerts
- Desktop notifications via `notify-send` and OpenClaw system events
- Web UI at `http://localhost:7070` — dashboard, inventory CRUD, print history, scanner, reports, admin
- Multi-user auth with roles: `admin`, `operator`, `viewer`
- Barcode and QR label generation for spools
- Mobile-friendly scanner page (PWA manifest included)
- PostgreSQL backend with Alembic migrations
- Docker Compose deployment with Redis rate-limiter backend

## Requirements

- Python 3.10+ **or** Docker + Docker Compose
- PostgreSQL 14+ (or use the bundled Docker Compose stack)
- Linux (uses `notify-send` for desktop notifications when running directly)
- `libnotify-bin` for desktop notifications: `sudo apt install libnotify-bin`
- OpenClaw CLI for system event notifications (optional)

---

## Quick Start — Docker Compose

This is the recommended way to run Bambu Tracker. It starts Postgres, Redis, and the app in one command.

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:
- Set `POSTGRES_PASSWORD` to a strong password
- Set `BAMBU_SECRET_KEY` to a random 32+ character string:
  ```bash
  python3 -c "import secrets; print(secrets.token_hex(32))"
  ```
- Set `BAMBU_ADMIN_PASS` for first-run admin creation
- Optionally set `CONFIG_PATH` to your `config.yaml` to enable live MQTT

### 2. Configure printers (optional — for live MQTT)

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml` with your printer credentials (see [Printer Configuration](#printer-configuration) below). Then set `CONFIG_PATH` in `.env`:

```
CONFIG_PATH=/absolute/path/to/your/config.yaml
```

If `CONFIG_PATH` is not set, the app starts in web-only mode (no MQTT, full inventory UI works).

### 3. Start

```bash
docker compose up -d
```

On first start with `BAMBU_CREATE_ADMIN=1`, the admin account is created automatically. After confirming login works, set `BAMBU_CREATE_ADMIN=0` in `.env` and restart.

The web UI is at **http://localhost:7070**.

---

## Quick Start — Local Python

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up PostgreSQL

Create a database and user:

```sql
CREATE USER bambu WITH PASSWORD 'yourpassword';
CREATE DATABASE bambu_tracker OWNER bambu;
```

### 3. Configure

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml`:
```yaml
database:
  url: "postgresql://bambu:yourpassword@localhost:5432/bambu_tracker"

printers:
  - name: "P1S-1"
    model: P1S
    serial: "01P00A123456789"
    access_token: "ABCD1234"
    region: us
```

### 4. Run migrations and create admin

```bash
alembic upgrade head
python run.py --create-admin
```

### 5. Run

```bash
python run.py
```

The web UI will be available at **http://localhost:7070**.

---

## Printer Configuration

### Getting your credentials

1. Open **Bambu Studio**
2. Select your printer from the device list
3. Click the gear/settings icon
4. **Access Code** → this is your `access_token`
5. **Serial Number** → visible in the same panel and on the printer's label

### Bambu Cloud token (for MQTT auth)

The MQTT broker requires your Bambu Cloud account credentials:

```yaml
cloud_auth:
  username: "your@email.com"
  token_file: "~/.bambu_token"   # file containing your Bambu Cloud JWT token
```

To obtain your cloud token:
1. Log in to [bambulab.com](https://bambulab.com) in a browser with devtools open
2. Look for the `Authorization: Bearer <token>` header in any authenticated API request
3. Save the token (the part after `Bearer `) to `~/.bambu_token`

Tokens expire; you will need to refresh this file periodically.

### Regions

| Region | Broker |
|--------|--------|
| `us` | `us.mqtt.bambulab.com:8883` |
| `eu` | `eu.mqtt.bambulab.com:8883` |
| `ap` | `ap.mqtt.bambulab.com:8883` |

---

## Web UI Pages

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/` | All printers, AMS slot status, active jobs |
| Inventory | `/inventory` | Spool list with remaining %, add/edit/delete/label |
| Scanner | `/scan` | Barcode/QR scan to look up or load/unload spools |
| Reports | `/reports` | Usage charts and CSV export |
| Admin | `/admin` | User management, audit log |
| Settings | `/settings` | Alert thresholds, printer list |

---

## Running Tests

SQLite smoke tests (no Postgres required):

```bash
pytest tests/test_sqlite_smoke.py -v
```

Full integration suite (requires a Postgres test instance):

```bash
TEST_DB_URL=postgresql://bambu:pass@localhost:5432/bambu_tracker_test pytest tests/ -v
```

Via Docker Compose:

```bash
docker compose --profile test run --rm test
```

---

## File Structure

```
bambu-tracker/
├── bambu_tracker/
│   ├── blueprints/          # Flask blueprints (auth, inventory, printers, scanner, reports, api, admin)
│   ├── static/              # CSS, JS, icons
│   ├── templates/           # Jinja2 HTML templates
│   ├── alerts.py            # notify-send + OpenClaw alerts
│   ├── auth.py              # Flask-Login, bcrypt, user CRUD
│   ├── config.py            # config.yaml loader/validator
│   ├── db.py                # SQLAlchemy Core schema + engine singleton
│   ├── inventory.py         # Business logic: spools, jobs, alerts, audit log
│   ├── labels.py            # Barcode/QR label generation
│   ├── limiter.py           # Flask-Limiter (Redis-backed in production)
│   ├── models.py            # Dataclasses: Printer, AmsSlot, FilamentSpool, PrintJob
│   ├── mqtt_client.py       # Bambu Cloud MQTT client with reconnect
│   └── web_ui.py            # Flask application factory
├── alembic/                 # DB migration scripts
├── docker/
│   ├── config.yaml.docker   # Fallback config for Docker (no-MQTT mode)
│   └── entrypoint.sh        # Container entrypoint (migrate → optionally enable MQTT → start)
├── tests/
│   ├── conftest.py          # Pytest fixtures (Postgres + Flask test client)
│   ├── test_inventory.py    # Inventory logic integration tests
│   ├── test_sqlite_smoke.py # In-process smoke tests (no Postgres needed)
│   └── test_web.py          # Route/API smoke tests
├── .env.example             # Docker Compose env template
├── config.yaml.example      # Printer + app config template
├── docker-compose.yml       # Postgres + Redis + app stack
├── Dockerfile               # Multi-stage Python 3.12 image
└── run.py                   # Entry point (MQTT threads + Flask)
```

---

## Database

PostgreSQL via SQLAlchemy Core (no ORM). Schema managed by Alembic.

```bash
# Create / migrate tables
alembic upgrade head

# Generate a new migration after schema changes
alembic revision --autogenerate -m "add foo"
```

Override the DB URL via env var:

```bash
BAMBU_DB_URL=postgresql://user:pass@host/dbname python run.py
```

Tables: `users`, `printers`, `printer_state`, `spools`, `spool_locations`, `print_jobs`, `stock_events`, `scan_events`, `labels`, `alerts`, `audit_log`

---

## Alerts

Two notification channels (each independently configurable in `config.yaml`):

- **Desktop** (`notify-send`): shown as system notifications
- **OpenClaw**: `openclaw system event --text "..." --mode now`

Alert types:
- Pre-print: insufficient stock for queued job
- Low stock: spool below configured threshold
- Print complete: usage summary
- Spool empty: needs replacement

---

## Notes on Bambu Cloud MQTT

Bambu Lab uses a private MQTT broker with TLS. The tracker connects using:
- Broker: `{region}.mqtt.bambulab.com:8883`
- Username: `bblp`
- Password: printer access token
- Topic: `device/{serial}/report`

Certificate verification is disabled (`CERT_NONE`) as Bambu uses a self-signed cert.
