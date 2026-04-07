"""Route smoke tests for Bambu Tracker Flask app.

Requires the same Postgres instance as test_inventory.py.
Run with:
    TEST_DB_URL=postgresql://bambu:bambu_dev_pass@localhost:5432/bambu_tracker_test pytest tests/ -v
"""
from __future__ import annotations


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Sign In" in resp.data


def test_unauthenticated_root_redirects_to_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_unauthenticated_inventory_redirects(client):
    resp = client.get("/inventory", follow_redirects=False)
    assert resp.status_code == 302


def test_unauthenticated_scan_redirects(client):
    resp = client.get("/scan", follow_redirects=False)
    assert resp.status_code == 302


def test_bad_login_stays_on_login(client):
    resp = client.post(
        "/login",
        data={"username": "nobody", "password": "wrong"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invalid" in resp.data


def test_login_and_dashboard(authed_client):
    resp = authed_client.get("/")
    assert resp.status_code == 200
    assert b"Bambu Tracker" in resp.data


def test_inventory_page_authenticated(authed_client):
    resp = authed_client.get("/inventory")
    assert resp.status_code == 200
    assert b"Filament" in resp.data


def test_api_printers_returns_json(authed_client):
    resp = authed_client.get("/api/printers")
    assert resp.status_code == 200
    assert resp.is_json
    assert isinstance(resp.get_json(), list)


def test_api_spools_returns_json(authed_client):
    resp = authed_client.get("/api/spools")
    assert resp.status_code == 200
    assert resp.is_json
    data = resp.get_json()
    assert "spools" in data or isinstance(data, list)


def test_add_spool_via_post(authed_client):
    resp = authed_client.post(
        "/inventory/add",
        data={
            "name": "Web Test Spool",
            "material": "PLA",
            "color": "#AABBCC",
            "brand": "TestBrand",
            "total_weight_g": "1000",
            "remaining_g": "1000",
            "low_stock_threshold_g": "50",
        },
        follow_redirects=False,
    )
    # Should redirect back to inventory list after successful add
    assert resp.status_code == 302


def test_scan_page_authenticated(authed_client):
    resp = authed_client.get("/scan")
    assert resp.status_code == 200
    assert b"Bambu Scanner" in resp.data


def test_scan_api_lookup_not_found(authed_client):
    resp = authed_client.post(
        "/api/scan",
        json={"barcode_id": "SPL99999", "action": "lookup"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["result"] == "not_found"
    assert data["spool"] is None


def test_scan_api_lookup_found(authed_client, inv):
    sid = inv.add_spool("ScanTestSpool", "PETG", "#009900", "ScanBrand", 800, 800)
    s = inv.get_spool_dict(sid)
    barcode = s["barcode_id"]
    resp = authed_client.post(
        "/api/scan",
        json={"barcode_id": barcode, "action": "lookup"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["result"] == "ok"
    assert data["spool"]["barcode_id"] == barcode


def test_manifest_json(client):
    resp = client.get("/manifest.json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["start_url"] == "/scan"


def test_scan_barcode_redirect_not_found(authed_client):
    resp = authed_client.get("/scan/SPLXXXXX", follow_redirects=False)
    assert resp.status_code == 404


def test_scan_barcode_redirect_found(authed_client, inv):
    sid = inv.add_spool("QRSpool", "ABS", "#FF0000", "", 500, 500)
    s = inv.get_spool_dict(sid)
    barcode = s["barcode_id"]
    resp = authed_client.get(f"/scan/{barcode}", follow_redirects=False)
    assert resp.status_code == 302
    assert str(sid) in resp.headers["Location"]
