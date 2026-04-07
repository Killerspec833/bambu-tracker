"""Integration tests for Inventory class (requires live Postgres via conftest fixtures).

Run with:
    TEST_DB_URL=postgresql://bambu:bambu_dev_pass@localhost:5432/bambu_tracker_test pytest tests/ -v
"""
from __future__ import annotations

import pytest


def test_barcode_sequence(inv):
    id1 = inv.add_spool("Spool A", "PLA", "#FF0000", "BrandX", 1000, 1000)
    id2 = inv.add_spool("Spool B", "PETG", "#00FF00", "BrandY", 750, 750)
    s1 = inv.get_spool_dict(id1)
    s2 = inv.get_spool_dict(id2)
    assert s1["barcode_id"].startswith("SPL")
    assert s2["barcode_id"].startswith("SPL")
    n1 = int(s1["barcode_id"][3:])
    n2 = int(s2["barcode_id"][3:])
    assert n2 == n1 + 1


def test_list_spools_filter(inv):
    inv.add_spool("FilterPLA", "PLA", "#AAAAAA", "Brand", 500, 500)
    rows, total = inv.list_spools(material="PLA")
    assert total >= 1
    assert all(r["material"] == "PLA" for r in rows)


def test_update_and_delete_spool(inv):
    sid = inv.add_spool("TempSpool", "ABS", "#FFFFFF", "", 500, 500)
    inv.update_spool(sid, name="TempSpool-updated", material="ABS")
    s = inv.get_spool_dict(sid)
    assert s["name"] == "TempSpool-updated"
    inv.delete_spool(sid)
    assert inv.get_spool_dict(sid) is None


def test_get_spool_by_barcode(inv):
    sid = inv.add_spool("BarcodeSpool", "PLA", "#123456", "Brand", 1000, 1000)
    s = inv.get_spool_dict(sid)
    bid = s["barcode_id"]
    found = inv.get_spool_by_barcode(bid)
    assert found is not None
    assert found["id"] == sid


def test_manual_adjust_records_event(inv):
    sid = inv.add_spool("AdjSpool", "PLA", "#AABBCC", "", 1000, 1000)
    inv.manual_adjust(sid, 750.0, note="test adjust")
    s = inv.get_spool_dict(sid)
    assert float(s["remaining_g"]) == 750.0
    history = inv.get_spool_history(sid)
    assert any(e["event_type"] == "manual_set" for e in history)


def test_upsert_and_list_printers(inv):
    inv.upsert_printer("TestPrinter", "P1S", "SERIAL001")
    printers = inv.list_printers()
    names = [p["name"] for p in printers]
    assert "TestPrinter" in names


def test_load_unload_spool(inv):
    sid = inv.add_spool("LoadSpool", "PLA", "#FF0000", "", 1000, 1000)
    pid = inv.upsert_printer("LoadPrinter", "P1S", "SERIAL002")
    result = inv.load_spool(sid, pid, ams_slot=0)
    assert result == "ok"
    # loading the same spool again → already_loaded
    result2 = inv.load_spool(sid, pid, ams_slot=1)
    assert result2 == "already_loaded"
    ok = inv.unload_spool(sid)
    assert ok is True
    loc = inv.get_active_location(sid)
    assert loc is None


def test_slot_conflict(inv):
    pid = inv.upsert_printer("ConflictPrinter", "P1S", "SERIAL003")
    sid1 = inv.add_spool("Conflict1", "PLA", "#111111", "", 1000, 1000)
    sid2 = inv.add_spool("Conflict2", "PLA", "#222222", "", 1000, 1000)
    assert inv.load_spool(sid1, pid, ams_slot=2) == "ok"
    # second spool into same slot → conflict
    assert inv.load_spool(sid2, pid, ams_slot=2) == "conflict"
    inv.unload_spool(sid1)


def test_alerts(inv):
    sid = inv.add_spool("AlertSpool", "PLA", "#000000", "", 100, 10)
    aid = inv.create_alert("low_stock", "Nearly empty", spool_id=sid)
    alerts = inv.get_active_alerts()
    ids = [a["id"] for a in alerts]
    assert aid in ids
    inv.acknowledge_alert(aid, user_id=1)
    alerts_after = inv.get_active_alerts()
    ids_after = [a["id"] for a in alerts_after]
    assert aid not in ids_after


def test_record_label_generation(inv):
    sid = inv.add_spool("LabelSpool", "PLA", "#FF00FF", "", 1000, 1000)
    label_id = inv.record_label_generation(sid, "code128")
    assert isinstance(label_id, int)
    label_id_qr = inv.record_label_generation(sid, "qr")
    assert label_id_qr > label_id


def test_spools_below_threshold(inv):
    sid = inv.add_spool("LowSpool", "PLA", "#FF0000", "", 1000, 30,
                        low_stock_threshold_g=50)
    inv.manual_adjust(sid, 30.0)
    low = inv.spools_below_threshold()
    ids = [s.id for s in low]
    assert sid in ids


def test_chart_usage_by_material(inv):
    result = inv.chart_usage_by_material(days=30)
    assert isinstance(result, list)


def test_chart_jobs_per_day(inv):
    result = inv.chart_jobs_per_day(days=30)
    assert isinstance(result, list)


def test_audit_log(inv):
    inv.record_audit("spool.create", entity_type="spool", entity_id=1)
    logs = inv.list_audit_log(limit=10, action_like="spool.create")
    assert len(logs) >= 1
    assert any(l["action"] == "spool.create" for l in logs)
