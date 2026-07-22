"""Test della capability whatsapp-notifications (funzioni pure, senza rete)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "notifier"))

import watchdog  # noqa: E402
import wa_client  # noqa: E402


def _state():
    return {"status": {}, "idle_alerted": {}}


def test_executed_emitted_once():
    prev = _state()
    s = [{"session_id": "a", "status": "OnWorking", "last_activity": 1000}]
    events, prev = watchdog.classify_events(s, prev, now=1000, idle_threshold=3600)
    assert events == []
    s = [{"session_id": "a", "status": "Finished", "last_activity": 1000}]
    events, prev = watchdog.classify_events(s, prev, now=1000, idle_threshold=3600)
    assert [e[0] for e in events] == ["executed"]
    # secondo giro, resta Finished -> niente
    events, prev = watchdog.classify_events(s, prev, now=1001, idle_threshold=3600)
    assert events == []


def test_stopped_emitted():
    prev = {"status": {"a": "OnWorking"}, "idle_alerted": {}}
    s = [{"session_id": "a", "status": "Killed", "last_activity": 1000}]
    events, prev = watchdog.classify_events(s, prev, now=1000, idle_threshold=3600)
    assert [e[0] for e in events] == ["stopped"]
    events, prev = watchdog.classify_events(s, prev, now=1001, idle_threshold=3600)
    assert events == []


def test_idle_emitted_once():
    prev = _state()
    # OnWorking, ultima attività 2 ore fa (now=10000, last=2800 -> 7200s > 3600)
    s = [{"session_id": "a", "status": "OnWorking", "last_activity": 2800}]
    events, prev = watchdog.classify_events(s, prev, now=10000, idle_threshold=3600)
    assert [e[0] for e in events] == ["idle"]
    # ancora idle -> soppresso
    events, prev = watchdog.classify_events(s, prev, now=10100, idle_threshold=3600)
    assert events == []


def test_render_idle_contains_name_and_minutes():
    pool = {"idle": ["{name}, {session_id} ferma da {minutes} minuti"]}
    agent = {"session_id": "sess1", "status": "OnWorking", "last_activity": 6400}
    msg = watchdog.render_message(
        "idle", agent, pool, name="Giuseppe", now=10000, rng=random.Random(0)
    )
    assert "Giuseppe" in msg
    assert "60" in msg  # (10000-6400)/60 = 60 minuti
    assert "{" not in msg  # nessun placeholder residuo


def test_local_pool_preferred(tmp_path):
    (tmp_path / "messages.default.json").write_text('{"idle": ["default"]}', encoding="utf-8")
    (tmp_path / "messages.local.json").write_text('{"idle": ["locale"]}', encoding="utf-8")
    pool = watchdog.load_pool(str(tmp_path))
    assert pool["idle"] == ["locale"]


def test_send_request_from_config_no_hardcoded_secrets():
    url, headers, body = wa_client.build_send_request(
        "ciao", "39333", base_url="http://gw:2785", session_id="s1", api_key="secret"
    )
    assert url == "http://gw:2785/api/sessions/s1/messages/send-text"
    assert body["to"] == "39333"
    assert body["text"] == "ciao"
    assert headers["Authorization"] == "Bearer secret"
    # nessun numero/chiave hardcoded nel modulo
    src = (ROOT / "notifier" / "wa_client.py").read_text(encoding="utf-8")
    import re
    assert not re.search(r"\b\d{10,15}\b", src)
    assert "sk-" not in src
