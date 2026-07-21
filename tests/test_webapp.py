"""Test per la webapp FastAPI (dashboard agent-registry)."""

import hashlib
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "webapp"))

import lock_manager as lm
import registry_manager as rm
import sync_manager as sm
import wiki_manager as wm
import main as webapp_main


@pytest.fixture(autouse=True)
def _no_real_legacy(monkeypatch, tmp_path):
    """Impedisce che i test tocchino un eventuale registry legacy reale su Desktop."""
    monkeypatch.setattr(
        rm, "_legacy_registry_path", lambda: tmp_path / "no-legacy" / "registry.md"
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Home del registry in directory temporanea via AGENT_REGISTRY_HOME."""
    h = tmp_path / "registry-home"
    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(h))
    monkeypatch.delenv("AGENT_REGISTRY_PATH", raising=False)
    # Reset override modulo lock_manager (altri test lo impostano)
    monkeypatch.setattr(lm, "LOCK_DIR", None)
    return h


@pytest.fixture
def bare_remote(tmp_path):
    """Remote bare locale per i test di setup sync."""
    remote = tmp_path / "remote.git"
    import subprocess

    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    return remote


@pytest.fixture
def client(home):
    return TestClient(webapp_main.app)


def _register(session_id, provider="Kimi", status="OnWorking", started_at=None, **kw):
    agent = rm.register_session(
        session_id=session_id,
        provider=provider,
        ai_version="2.7",
        working_on=f"lavoro {session_id}",
    )
    updates = {}
    if status != "OnWorking":
        updates["status"] = status
    if started_at:
        updates["started_at"] = started_at
    if updates:
        rm.update_session(session_id, **updates)
    return agent


def _today_rome() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")


# --- 6.1: filtri sessioni ---


def test_sessions_default_today_onworking(client):
    """Default: solo OnWorking di oggi."""
    _register("oggi-1")
    _register("ieri-1", started_at="2020-01-01 10:00")
    _register("stop-oggi", status="Stop")
    res = client.get("/api/sessions")
    assert res.status_code == 200
    data = res.json()
    ids = [s["session_id"] for s in data["sessions"]]
    assert ids == ["oggi-1"]
    assert data["date"] == _today_rome()
    assert data["status"] == "OnWorking"


def test_sessions_filter_date(client):
    """Filtro data esplicita: tutte le sessioni di quella data (ogni status)."""
    _register("old-1", started_at="2020-01-01 10:00")
    _register("old-2", status="Finished", started_at="2020-01-01 11:00")
    _register("oggi-1")
    res = client.get("/api/sessions", params={"date": "2020-01-01"})
    ids = sorted(s["session_id"] for s in res.json()["sessions"])
    assert ids == ["old-1", "old-2"]


def test_sessions_filter_status_all_dates(client):
    """Status senza date → tutte le date per quello status."""
    _register("fin-oggi", status="Finished")
    _register("fin-old", status="Finished", started_at="2020-01-01 10:00")
    _register("work-1")
    res = client.get("/api/sessions", params={"status": "Finished"})
    data = res.json()
    ids = sorted(s["session_id"] for s in data["sessions"])
    assert ids == ["fin-oggi", "fin-old"]
    assert data["date"] is None


def test_sessions_filter_provider(client):
    """Filtro provider."""
    _register("k-1", provider="Kimi")
    _register("c-1", provider="Claude")
    res = client.get(
        "/api/sessions", params={"provider": "Claude", "status": "OnWorking"}
    )
    ids = [s["session_id"] for s in res.json()["sessions"]]
    assert ids == ["c-1"]


def test_sessions_all_bypasses_default(client):
    """all=true: nessun default di data, tutte le sessioni."""
    _register("oggi-1")
    _register("old-1", status="Stop", started_at="2020-01-01 10:00")
    res = client.get("/api/sessions", params={"all": "true"})
    ids = sorted(s["session_id"] for s in res.json()["sessions"])
    assert ids == ["oggi-1", "old-1"]


def test_api_registry_retrocompat(client):
    """GET /api/registry continua a restituire tutte le sessioni."""
    _register("a-1")
    _register("a-2", status="Finished")
    res = client.get("/api/registry")
    assert res.status_code == 200
    assert len(res.json()["agents"]) == 2


# --- 6.2: azioni ---


def test_kill_via_api_logical_stop(client, home):
    """Kill su PID non locale: stop logico, risposta lo indica."""
    _register("victim-1")
    rm.update_session("victim-1", pid=999999, do_not_touch=[])
    res = client.post("/api/sessions/victim-1/kill", json={})
    assert res.status_code == 200
    data = res.json()
    assert data["killed"] is True
    assert data["terminated"] is False
    assert data["action"] == "logical_stop"
    agent = rm.find_agent("victim-1")
    assert agent["status"] == "Killed"


def test_kill_via_api_process_terminated(client, home):
    """Kill reale: processo finto con cmdline compatibile (D6)."""
    import subprocess

    proc = subprocess.Popen(["sleep", "60"])
    try:
        _register("victim-2")
        rm.update_session(
            "victim-2",
            pid=proc.pid,
            cmdline=f"sleep 60 victim-2",
        )
        res = client.post("/api/sessions/victim-2/kill", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["killed"] is True
        assert data["terminated"] is True
        assert data["action"] == "process_terminated"
        assert rm.find_agent("victim-2")["status"] == "Killed"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_kill_session_not_found(client):
    res = client.post("/api/sessions/inesistente/kill", json={})
    assert res.status_code == 404


def test_force_release_stale_lock(client, home):
    """Lock stale: rilascio immediato senza confirm."""
    target = str(home / "file-a.txt")
    _register("owner-1")
    rm.update_session("owner-1", do_not_touch=[target])
    # Lock file stale scritto a mano (timestamp 0)
    lock_file = lm._lock_file(target)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("owner-1|0", encoding="utf-8")

    res = client.post(
        "/api/locks/force-release",
        json={"path": target, "session_id": "owner-1"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["released"] is True
    assert data["stale"] is True
    assert not lock_file.exists()
    # do_not_touch della sessione owner ripulito
    assert rm.find_agent("owner-1")["do_not_touch"] == []


def test_force_release_live_lock_requires_confirm(client, home):
    """Lock non stale: 409 senza confirm, poi rilascio con confirm=true."""
    target = str(home / "file-b.txt")
    _register("owner-2")
    lm.acquire_lock(target, "owner-2")

    res = client.post(
        "/api/locks/force-release",
        json={"path": target, "session_id": "owner-2"},
    )
    assert res.status_code == 409
    assert "warning" in res.json()
    assert lm.is_locked(target)["locked"] is True

    res2 = client.post(
        "/api/locks/force-release",
        json={"path": target, "session_id": "owner-2", "confirm": True},
    )
    assert res2.status_code == 200
    assert res2.json()["released"] is True
    assert lm.is_locked(target)["locked"] is not True


def test_force_release_owner_mismatch(client, home):
    """session_id diverso dall'owner reale → 409."""
    target = str(home / "file-c.txt")
    _register("owner-3")
    lm.acquire_lock(target, "owner-3")
    res = client.post(
        "/api/locks/force-release",
        json={"path": target, "session_id": "altro", "confirm": True},
    )
    assert res.status_code == 409
    assert res.json()["error"] == "owner mismatch"


def test_cleanup_via_api(client, home):
    """Cleanup: sessioni zombie (PID morto, nessun lock) marcate Stop."""
    _register("zombie-1")
    rm.update_session("zombie-1", pid=999999)
    _register("viva-1")
    # viva-1 ha un lock fresco → non viene toccata dal cleanup
    target = str(home / "file-d.txt")
    lm.acquire_lock(target, "viva-1")

    res = client.post("/api/cleanup")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert data["stopped"] == ["zombie-1"]
    assert rm.find_agent("zombie-1")["status"] == "Stop"
    assert rm.find_agent("viva-1")["status"] == "OnWorking"


def test_list_locks_endpoint(client, home):
    """GET /api/locks elenca i lock attivi con flag stale."""
    _register("owner-4")
    target = str(home / "file-e.txt")
    lm.acquire_lock(target, "owner-4")
    res = client.get("/api/locks")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    lock = data["locks"][0]
    assert lock["path"] == target
    assert lock["session_id"] == "owner-4"
    assert lock["stale"] is False


# --- 6.3: wiki ---


def _make_entry(session_id, router, cosa="fatto qualcosa", bug=None, data="2026-07-20"):
    return wm.upsert_entry(
        session_id,
        {
            "provider": "Kimi",
            "modello": "k2",
            "data": data,
            "router": router,
            "cosa_fatto": cosa,
            "come_fatto": "con cura",
            "problema_risolto": "nessuno",
            "bug_trovati": bug or [],
        },
    )


def test_wiki_search(client, home):
    """Ricerca FTS su router/cosa_fatto/bug_trovati."""
    _make_entry("s-1", router="fix login oauth")
    _make_entry("s-2", router="refactor dashboard", bug=["bug nel parser"])
    res = client.get("/api/wiki", params={"q": "oauth"})
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert data["entries"][0]["session_id"] == "s-1"


def test_wiki_search_bug_field(client, home):
    """La ricerca copre anche bug_trovati."""
    _make_entry("s-1", router="fix login")
    _make_entry("s-2", router="refactor dashboard", bug=["race condition nel lock"])
    res = client.get("/api/wiki", params={"q": "race"})
    ids = [e["session_id"] for e in res.json()["entries"]]
    assert ids == ["s-2"]


def test_wiki_empty_query_returns_latest(client, home):
    """q vuoto → ultime N entry (id desc)."""
    _make_entry("s-1", router="primo")
    _make_entry("s-2", router="secondo")
    res = client.get("/api/wiki", params={"q": ""})
    data = res.json()
    assert data["count"] == 2
    assert [e["session_id"] for e in data["entries"]] == ["s-2", "s-1"]


def test_wiki_entry_detail(client, home):
    """Dettaglio completo entry per id."""
    entry_id = _make_entry("s-1", router="dettaglio", bug=["bug x"])
    res = client.get(f"/api/wiki/{entry_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == entry_id
    assert data["session_id"] == "s-1"
    assert data["router"] == "dettaglio"
    assert data["bug_trovati"] == ["bug x"]
    assert "come_fatto" in data
    assert "context_md" in data


def test_wiki_entry_not_found(client, home):
    res = client.get("/api/wiki/9999")
    assert res.status_code == 404


def test_sync_endpoint(client):
    """GET /api/sync restituisce lo stato del git-sync (disabilitato in tmp home)."""
    res = client.get("/api/sync")
    assert res.status_code == 200
    assert res.json()["enabled"] is False
# --- sync setup endpoint ---


def test_sync_init_ok_empty_remote(client, home, bare_remote):
    """4.1/4.3: setup su remote vuoto → init ok."""
    res = client.post("/api/sync/init", json={"url": f"file://{bare_remote}"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["branch"] == "init"
    assert sm.is_git_enabled(home) is True


def test_sync_init_error_malformed_url(client, home):
    """4.1/4.3: URL malformato → 400, nessun side-effect."""
    res = client.post("/api/sync/init", json={"url": "not-a-git-url"})
    assert res.status_code == 400
    data = res.json()
    assert data["status"] == "error"
    assert not (home / ".git").exists()


def test_sync_init_needs_confirm_public(client, home, bare_remote, monkeypatch):
    """4.1/4.3: repo pubblico senza conferma → needs_confirm, no side-effect."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(sm, "check_github_visibility", lambda url, token=None: "public")
    monkeypatch.setattr(sm, "_is_github_host", lambda url: True)

    res = client.post("/api/sync/init", json={"url": f"file://{bare_remote}"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "needs_confirm"
    assert data["reason"] == "public_repo"
    assert not (home / ".git").exists()


def test_sync_init_already_configured(client, home, bare_remote):
    """4.1/4.3: setup su home già configurata → ok no-op."""
    sm.init_git_sync(f"file://{bare_remote}", home)
    res = client.post("/api/sync/init", json={"url": f"file://{bare_remote}"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["branch"] is None
    assert "già configurato" in data["message"]


def test_sync_init_needs_confirm_merge(client, home, tmp_path):
    """4.1/4.3: remote popolato + home git locale → needs_confirm merge."""
    # crea remote popolato
    remote = tmp_path / "remote.git"
    import subprocess

    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "-C", str(work), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@e"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "T"], check=True, capture_output=True)
    (work / "sessions").mkdir()
    (work / "sessions" / "r.yaml").write_text("id: r\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", "i"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", str(remote), "main"], check=True, capture_output=True)

    # home git locale
    home.mkdir(parents=True)
    subprocess.run(["git", "-C", str(home), "init"], check=True, capture_output=True)

    res = client.post("/api/sync/init", json={"url": f"file://{remote}"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "needs_confirm"
    assert data["reason"] == "merge_with_local_data"
    assert data["branch"] == "integrazione"
# --- setup card UI ---


def test_setup_card_present_in_index_html(client):
    """5.1: la pagina principale contiene la setup card per il multi-macchina."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert "setup-card" in html
    assert '/api/sync/init' in html
    assert "Configura multi-macchina" in html
    assert "setup-confirm-public" in html
    assert "setup-confirm-merge" in html


def test_setup_card_hidden_when_sync_enabled(client, home, bare_remote):
    """5.2: quando il sync è abilitato la dashboard non mostra la setup card."""
    sm.init_git_sync(f"file://{bare_remote}", home)
    res = client.get("/api/sync")
    assert res.json()["enabled"] is True
    # La logica JS nasconde la card in base a GET /api/sync; l'HTML resta presente.
    res = client.get("/")
    assert "setup-card" in res.text
