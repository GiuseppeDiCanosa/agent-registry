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


def test_started_emitted_once():
    prev = _state()
    s = [{"session_id": "a", "status": "OnWorking", "last_activity": 1000}]
    events, prev = watchdog.classify_events(s, prev, now=1000, idle_threshold=3600)
    assert [e[0] for e in events] == ["started"]
    # secondo giro, resta OnWorking -> niente
    events, prev = watchdog.classify_events(s, prev, now=1001, idle_threshold=3600)
    assert events == []


def test_started_not_emitted_on_cold_start():
    s = [{"session_id": "a", "status": "OnWorking", "last_activity": 1000}]
    events, state = watchdog.classify_events(
        s, _state(), now=1000, idle_threshold=3600, cold_start=True
    )
    assert events == []
    assert state["status"]["a"] == "OnWorking"
    # dopo il cold-start, un nuovo cambiamento genera regolarmente il suo evento
    s2 = [
        {"session_id": "a", "status": "OnWorking", "last_activity": 1000},
        {"session_id": "b", "status": "OnWorking", "last_activity": 1000},
    ]
    events2, _ = watchdog.classify_events(s2, state, now=1001, idle_threshold=3600)
    assert [e[0] for e in events2] == ["started"]  # solo b


def test_executed_emitted_once():
    prev = _state()
    s = [{"session_id": "a", "status": "OnWorking", "last_activity": 1000}]
    events, prev = watchdog.classify_events(s, prev, now=1000, idle_threshold=3600)
    assert [e[0] for e in events] == ["started"]  # prima comparsa OnWorking
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
    assert [e[0] for e in events] == ["started", "idle"]  # prima comparsa + idle
    # ancora idle -> soppresso
    events, prev = watchdog.classify_events(s, prev, now=10100, idle_threshold=3600)
    assert events == []


def test_cold_start_seeds_without_events():
    s = [
        {"session_id": "a", "status": "Finished", "last_activity": 1000},
        {"session_id": "b", "status": "OnWorking", "last_activity": 2800},  # idle a now=10000
    ]
    events, state = watchdog.classify_events(
        s, _state(), now=10000, idle_threshold=3600, cold_start=True
    )
    assert events == []  # nessuna notifica storica
    assert state["status"]["a"] == "Finished"
    assert state["status"]["b"] == "OnWorking"
    # dopo il cold-start, solo un cambiamento NUOVO genera eventi
    s2 = s + [{"session_id": "c", "status": "Finished", "last_activity": 1000}]
    events2, _ = watchdog.classify_events(s2, state, now=10001, idle_threshold=3600)
    assert [e[0] for e in events2] == ["executed"]  # solo c


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
    assert body["chatId"] == "39333@c.us"
    assert body["text"] == "ciao"
    assert headers["X-API-Key"] == "secret"
    # nessun numero/chiave hardcoded nel modulo
    src = (ROOT / "notifier" / "wa_client.py").read_text(encoding="utf-8")
    import re
    assert not re.search(r"\b\d{10,15}\b", src)
    assert "sk-" not in src


# --- Firma applicata a runtime dal provider della sessione ---


def test_signature_is_session_provider():
    pool = {"idle": ["{session_id} ferma da {minutes} minuti"]}
    agent = {"session_id": "s1", "provider": "Claude", "last_activity": 6400}
    msg = watchdog.render_message("idle", agent, pool, now=10000, rng=random.Random(0))
    assert msg.endswith("— Claude")


def test_unknown_provider_signs_without_code_change():
    """Un provider mai visto prima firma col proprio nome: nessun elenco nel codice."""
    pool = {"executed": ["fatto"]}
    for provider in ("Claude", "Codex", "Gemini", "UnProviderNuovo"):
        agent = {"session_id": "s1", "provider": provider}
        msg = watchdog.render_message("executed", agent, pool, rng=random.Random(0))
        assert msg.endswith(f"— {provider}")
    src = (ROOT / "notifier" / "watchdog.py").read_text(encoding="utf-8")
    assert "Codex" not in src and "Claude" not in src


def test_no_signature_when_provider_missing():
    pool = {"executed": ["fatto"]}
    msg = watchdog.render_message("executed", {"session_id": "s1"}, pool, rng=random.Random(0))
    assert msg == "fatto"


def test_default_pool_carries_no_signatures():
    """Le firme non stanno nelle stringhe: le appone il watchdog a runtime."""
    import json

    pool = json.loads(
        (ROOT / "notifier" / "messages.default.json").read_text(encoding="utf-8")
    )
    for event, messages in pool.items():
        for text in messages:
            assert "—" not in text, f"firma nel pool di default, evento {event}: {text}"


# --- Precedenza del testo composto dall'agente ---


def test_composed_text_wins_over_pool():
    pool = {"executed": ["testo del pool"]}
    agent = {
        "session_id": "s1",
        "provider": "Claude",
        "notify": {"executed": "Ho chiuso il refactor, capo."},
    }
    msg = watchdog.render_message("executed", agent, pool, rng=random.Random(0))
    assert msg.startswith("Ho chiuso il refactor, capo.")
    assert "testo del pool" not in msg
    assert msg.endswith("— Claude")


def test_composed_text_only_for_its_own_event():
    """Un testo composto per `started` non deve essere speso per `executed`."""
    pool = {"executed": ["testo del pool"]}
    agent = {
        "session_id": "s1",
        "provider": "Codex",
        "notify": {"started": "Parto adesso."},
    }
    msg = watchdog.render_message("executed", agent, pool, rng=random.Random(0))
    assert msg.startswith("testo del pool")
    assert "Parto adesso." not in msg


def test_composed_text_is_verbatim():
    """Nessuna sostituzione di placeholder dentro al testo dell'agente."""
    pool = {"executed": ["ignorato"]}
    agent = {
        "session_id": "s1",
        "provider": "Claude",
        "notify": {"executed": "Ho scritto {name} apposta."},
    }
    msg = watchdog.render_message("executed", agent, pool, rng=random.Random(0))
    assert "{name}" in msg


# --- Consumo distruttivo del testo composto ---


def _session_file(home, session_id, notify, provider="Claude"):
    import yaml

    sessions = home / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / f"{session_id}.yaml"
    path.write_text(
        yaml.safe_dump(
            {"session_id": session_id, "provider": provider,
             "status": "OnWorking", "notify": notify},
            sort_keys=False, allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def test_consume_removes_only_its_event(tmp_path, monkeypatch):
    import yaml

    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(tmp_path))
    path = _session_file(tmp_path, "s1", {"started": "ciao", "executed": "fatto"})
    assert watchdog.consume_notify(tmp_path, "s1", "executed") is True
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["notify"] == {"started": "ciao"}


def test_consume_drops_empty_notify_map(tmp_path, monkeypatch):
    import yaml

    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(tmp_path))
    path = _session_file(tmp_path, "s2", {"executed": "fatto"})
    assert watchdog.consume_notify(tmp_path, "s2", "executed") is True
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "notify" not in data


def test_consume_is_idempotent(tmp_path, monkeypatch):
    """Un solo invio per composizione: al secondo giro non c'è più nulla."""
    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(tmp_path))
    _session_file(tmp_path, "s3", {"executed": "fatto"})
    assert watchdog.consume_notify(tmp_path, "s3", "executed") is True
    assert watchdog.consume_notify(tmp_path, "s3", "executed") is False


def test_consume_preserves_mtime(tmp_path, monkeypatch):
    """L'mtime è la sorgente di last_activity: il consumo non deve toccarlo."""
    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(tmp_path))
    path = _session_file(tmp_path, "s4", {"executed": "fatto"})
    before = path.stat().st_mtime
    os_utime_target = before - 7200  # due ore fa: la sessione sarebbe idle
    import os as _os

    _os.utime(path, (os_utime_target, os_utime_target))
    assert watchdog.consume_notify(tmp_path, "s4", "executed") is True
    assert path.stat().st_mtime == os_utime_target


def test_consume_gives_up_when_locked(tmp_path, monkeypatch):
    """Lock tenuto da un altro: nessuna eccezione, nessuna riscrittura."""
    import yaml

    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(tmp_path))
    path = _session_file(tmp_path, "s5", {"executed": "fatto"})
    lm = watchdog._lock_manager()
    lm.acquire_lock(str(path), "un-altro-agente")
    try:
        assert watchdog.consume_notify(tmp_path, "s5", "executed") is False
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["notify"] == {"executed": "fatto"}
    finally:
        lm.release_lock(str(path), "un-altro-agente")


def test_consume_missing_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(tmp_path))
    assert watchdog.consume_notify(tmp_path, "mai-esistita", "executed") is False


# --- Consegna: si consuma solo dopo un invio riuscito ---


def test_failed_send_keeps_composed_text(tmp_path, monkeypatch):
    import yaml

    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(tmp_path))
    path = _session_file(tmp_path, "s6", {"executed": "Ho chiuso il refactor."})

    def boom(text, recipient, **kw):
        raise RuntimeError("gateway irraggiungibile")

    monkeypatch.setattr(watchdog.wa_client, "send_text", boom)
    agent = yaml.safe_load(path.read_text(encoding="utf-8"))
    watchdog.deliver_events([("executed", agent)], tmp_path, {}, recipient="39333")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["notify"] == {"executed": "Ho chiuso il refactor."}


def test_successful_send_consumes_composed_text(tmp_path, monkeypatch):
    import yaml

    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(tmp_path))
    path = _session_file(tmp_path, "s7", {"executed": "Ho chiuso il refactor."})
    sent = []
    monkeypatch.setattr(
        watchdog.wa_client, "send_text", lambda text, rec, **kw: sent.append(text)
    )
    agent = yaml.safe_load(path.read_text(encoding="utf-8"))
    watchdog.deliver_events([("executed", agent)], tmp_path, {}, recipient="39333")

    assert sent == ["Ho chiuso il refactor.\n— Claude"]
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "notify" not in data


def test_pool_message_is_not_consumed(tmp_path, monkeypatch):
    """Non c'è nulla da consumare quando il testo viene dal pool."""
    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(tmp_path))
    sent = []
    monkeypatch.setattr(
        watchdog.wa_client, "send_text", lambda text, rec, **kw: sent.append(text)
    )
    agent = {"session_id": "s8", "provider": "Codex"}
    watchdog.deliver_events(
        [("stopped", agent)], tmp_path, {"stopped": ["fermo"]}, recipient="39333"
    )
    assert sent == ["fermo\n— Codex"]
