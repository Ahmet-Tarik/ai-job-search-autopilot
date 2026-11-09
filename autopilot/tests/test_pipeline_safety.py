"""Regression tests: frozen Telegram shortlists, strict direct-route matching, excluded companies,
config isolation of benchmarks, local-model gating. No network, no model calls, temp state only."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
import local_llm  # noqa: E402
import posting_status as ps  # noqa: E402
import shortlist  # noqa: E402
import telegram_inbox as ti  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "config.json"


def _isolate(monkeypatch, tmp):
    monkeypatch.setattr(shortlist, "PUBLISHED_DIR", tmp / "shortlists")
    monkeypatch.setattr(shortlist, "INDEX", tmp / "index.json")
    monkeypatch.setattr(ti, "SELECTIONS", tmp / "sel.json")
    monkeypatch.setattr(ti, "log_event", lambda *a, **k: None)


def _item(n, key, company):
    return {"n": n, "key": key, "company": company, "title": "Junior Developer", "url": f"https://x/{key}", "category": "junior"}


def test_reply_to_older_list_selects_that_lists_job(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    a = shortlist.publish({"date": "2026-11-09", "items": [_item(1, "a1", "Alpha"), _item(2, "a2", "Beta")]}, message_ids=[1001])
    b = shortlist.publish({"date": "2026-11-10", "items": [_item(1, "b1", "Gamma"), _item(2, "a1", "Alpha")]}, message_ids=[2001])
    ti.handle("apply", [1], reply_to=1001)          # old list -> Alpha
    ti.handle("apply", [1])                          # no reply -> latest list -> Gamma
    sel = json.loads((tmp_path / "sel.json").read_text())
    assert (sel[0]["key"], sel[0]["list_id"]) == ("a1", a)
    assert (sel[1]["key"], sel[1]["list_id"]) == ("b1", b)
    f = shortlist.PUBLISHED_DIR / f"{a}.json"
    assert oct(f.stat().st_mode)[-3:] == "444"
    assert shortlist.publish({"date": "2026-11-09", "items": []}) != a   # republishing never overwrites


def test_excluded_company_cannot_be_selected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    shortlist.publish({"date": "2026-11-10", "items": [_item(1, "sp", "Example Excluded Co")]})
    out = ti.handle("apply", [1])
    assert "permanently excluded" in out
    assert json.loads((tmp_path / "sel.json").read_text()) == []


def _job(t, loc, wp="", req="", i="1"):
    return {"id": i, "req": req, "title": t, "url": f"https://jobs.lever.co/acme/{i}", "location": loc, "workplace": wp}


BOARDS = [{"ats": "lever", "slug": "acme", "board_name": None, "identity": "verified slug", "jobs": [
    _job("Software Engineer - Backend (New Grad)", "Istanbul, Turkey", "on-site", i="a"),
    _job("Junior Python Developer", "Istanbul", "on-site", i="c"), _job("Junior Python Developer", "Berlin, Germany", i="d"),
    _job("Data Engineer", "Remote - Turkey", "remote", req="R-1001", i="e"),
    _job("Software Engineer, Backend (New Grad)", "Full-time", "onsite", i="p")]},
    {"ats": "greenhouse", "slug": "acme", "board_name": "Acme Robotics GmbH", "identity": None,
     "jobs": [_job("QA Engineer", "Ankara", i="g")]}]


def test_direct_route_strict_matching():
    m = lambda t, loc="", txt="": ps.match_direct_route("Acme", t, loc, txt, boards=BOARDS)
    assert m("Junior Python Developer", "Istanbul, Turkey")["posting_id"] == "c"
    assert not m("Junior Python Developer")["match"]                      # ambiguous
    assert not m("Junior Python Developer", "Paris, France")["match"]     # location mismatch
    assert not m("Senior Python Developer", "Istanbul")["match"]          # never a similar title
    assert not m("QA Engineer", "Ankara")["match"]                        # employer identity unconfirmed
    assert not m("Data Engineer", "Istanbul (On-site)")["match"]          # work-mode contradiction
    assert not m("Data Engineer", "Turkey", "Job ID: R-2002")["match"]    # requisition mismatch
    assert m("Data Engineer", "Turkey", "Req ID: R-1001")["posting_id"] == "e"
    # Peak-style board with a contract word in the location field
    assert ps.match_direct_route("Acme", "Software Engineer, Backend (New Grad)", "Istanbul, Türkiye",
                                 boards=[{**BOARDS[0], "jobs": [BOARDS[0]["jobs"][4]]}])["posting_id"] == "p"


def test_benchmark_style_override_never_touches_production_config():
    before = CONFIG.read_text()
    orig = local_llm._cfg
    try:
        local_llm._cfg = lambda: {**orig(), "model": "qwen3.5:35b", "enabled": True}   # what bench_compare.py does
        assert local_llm._cfg()["model"] == "qwen3.5:35b"
    finally:
        local_llm._cfg = orig
    assert CONFIG.read_text() == before
    assert json.loads(before)["local_llm"]["model"] == "qwen3.5:9b"


def test_production_model_is_9b_and_35b_is_the_documented_fallback():
    cfg = json.loads(CONFIG.read_text())["local_llm"]
    assert cfg["model"] == "qwen3.5:9b"
    assert cfg["fallback_model_installed"] == "qwen3.5:35b"
    assert cfg["min_mem_free_pct"] >= 8 and cfg["audit_every_nth_rejection"] >= 1


def test_screening_is_skipped_while_another_model_is_loaded(monkeypatch):
    def fake_get(path, timeout=5):
        if path == "/api/tags":
            return {"models": [{"name": "qwen3.5:9b"}, {"name": "qwen3.5:35b"}]}
        return {"models": [{"name": "qwen3.5:35b"}]}       # 35B still resident
    monkeypatch.setattr(local_llm, "_get", fake_get)
    monkeypatch.setattr(local_llm, "mem_free_pct", lambda: 80)
    st = local_llm.status()
    assert st["other_model_loaded"] == "qwen3.5:35b" and not st["usable"]


def test_low_memory_disables_screening(monkeypatch):
    monkeypatch.setattr(local_llm, "_get", lambda p, timeout=5: {"models": [{"name": "qwen3.5:9b"}]} if p == "/api/tags" else {"models": []})
    monkeypatch.setattr(local_llm, "mem_free_pct", lambda: 3)
    assert not local_llm.status()["usable"]


def test_only_local_audit_may_write_config():
    root = Path(__file__).resolve().parents[1]
    writers = [p.name for p in root.glob("*.py") if "config.json" in p.read_text() and "write_text" in p.read_text()
               and "config.json\"" in p.read_text() and p.name not in ("common.py",)]
    assert writers == ["local_audit.py"], writers
