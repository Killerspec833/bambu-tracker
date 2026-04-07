from __future__ import annotations

from bambu_tracker.auth import create_user
from bambu_tracker.db import get_engine, init_engine, metadata
from bambu_tracker.inventory import Inventory
from bambu_tracker.web_ui import create_app


def _build_sqlite_app():
    init_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(get_engine())

    inv = Inventory()
    app = create_app(inv, {}, {}, secret_key="test-secret")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    user_id = create_user("admin", "admin@example.com", "password123", role="admin")
    printer_id = inv.upsert_printer("P1", "P1S", "SERIAL001")
    spool_id = inv.add_spool(
        "Smoke Spool",
        "PLA",
        "#ffffff",
        "Brand",
        1000,
        1000,
        created_by=user_id,
    )
    inv.record_audit("spool.create", user_id=user_id, entity_type="spool", entity_id=spool_id)
    inv.create_alert("low_stock", "Nearly empty", spool_id=spool_id, printer_id=printer_id)
    inv.log_print_job("P1", "Benchy", None, None, "FINISH", {0: 25.0})

    client = app.test_client()
    resp = client.post(
        "/login",
        data={"username": "admin", "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    return client, inv, spool_id, printer_id


def test_init_engine_supports_sqlite():
    engine = init_engine("sqlite+pysqlite:///:memory:")
    assert engine.dialect.name == "sqlite"


def test_sqlite_smoke_routes_render():
    client, _, spool_id, printer_id = _build_sqlite_app()

    for path in (
        "/",
        "/inventory",
        f"/inventory/{spool_id}",
        "/reports",
        "/admin",
        "/admin/audit",
        "/scan",
        f"/printers/{printer_id}",
    ):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 200, path


def test_sqlite_scan_api_round_trip():
    client, inv, spool_id, printer_id = _build_sqlite_app()
    barcode_id = inv.get_spool_dict(spool_id)["barcode_id"]

    lookup = client.post("/api/scan", json={"barcode_id": barcode_id, "action": "lookup"})
    assert lookup.status_code == 200
    assert lookup.get_json()["result"] == "ok"

    load = client.post(
        "/api/scan",
        json={"barcode_id": barcode_id, "action": "load", "printer_id": printer_id, "ams_slot": 1},
    )
    assert load.status_code == 200
    assert load.get_json()["result"] == "ok"

    unload = client.post("/api/scan", json={"barcode_id": barcode_id, "action": "unload"})
    assert unload.status_code == 200
    assert unload.get_json()["result"] == "ok"
