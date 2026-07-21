"""Test per la sincronizzazione lock ↔ registry (D5) e il batch check (gruppo 3)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import lock_manager as lm
import registry_manager as rm


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Home registry e lock dir isolate in tmp; niente contatti col registry reale."""
    home = tmp_path / "registry-home"
    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(home))
    monkeypatch.delenv("AGENT_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(
        rm, "_legacy_registry_path", lambda: tmp_path / "no-legacy" / "registry.md"
    )
    monkeypatch.setattr(lm, "LOCK_DIR", None)
    lm._OPEN_LOCK_FDS.clear()
    yield home
    lm._OPEN_LOCK_FDS.clear()


# --- 3.2: lock ↔ registry sincronizzati ---


def test_acquire_updates_do_not_touch_and_space(isolated_home):
    rm.register_session("sid-1", "Kimi", "2.7", "Task", space=["old.py"])
    result = lm.acquire_lock("/tmp/sync-a.txt", "sid-1")
    assert result["locked"] is True
    agent = rm.find_agent("sid-1")
    assert "/tmp/sync-a.txt" in agent["do_not_touch"]
    assert "/tmp/sync-a.txt" in agent["space"]
    assert "old.py" in agent["space"]


def test_acquire_is_idempotent_in_registry(isolated_home):
    rm.register_session("sid-1", "Kimi", "2.7", "Task")
    lm.acquire_lock("/tmp/sync-b.txt", "sid-1")
    lm.acquire_lock("/tmp/sync-b.txt", "sid-1")  # already locked by you
    agent = rm.find_agent("sid-1")
    assert agent["do_not_touch"].count("/tmp/sync-b.txt") == 1
    assert agent["space"].count("/tmp/sync-b.txt") == 1


def test_release_removes_from_do_not_touch(isolated_home):
    rm.register_session("sid-1", "Kimi", "2.7", "Task")
    lm.acquire_lock("/tmp/sync-c.txt", "sid-1")
    release = lm.release_lock("/tmp/sync-c.txt", "sid-1")
    assert release["released"] is True
    agent = rm.find_agent("sid-1")
    assert "/tmp/sync-c.txt" not in agent["do_not_touch"]
    # space resta (la sessione ci ha comunque lavorato)
    assert "/tmp/sync-c.txt" in agent["space"]


def test_acquire_failed_lock_does_not_touch_registry(isolated_home):
    rm.register_session("sid-1", "Kimi", "2.7", "Task")
    rm.register_session("sid-2", "Claude", "4.8", "Altro")
    lm.acquire_lock("/tmp/sync-d.txt", "sid-1")
    result = lm.acquire_lock("/tmp/sync-d.txt", "sid-2")
    assert result["locked"] is False
    agent = rm.find_agent("sid-2")
    assert "/tmp/sync-d.txt" not in (agent["do_not_touch"] or [])
    assert "/tmp/sync-d.txt" not in (agent["space"] or [])


def test_release_by_non_owner_keeps_registry(isolated_home):
    rm.register_session("sid-1", "Kimi", "2.7", "Task")
    lm.acquire_lock("/tmp/sync-e.txt", "sid-1")
    result = lm.release_lock("/tmp/sync-e.txt", "sid-intruso")
    assert result["released"] is False
    agent = rm.find_agent("sid-1")
    assert "/tmp/sync-e.txt" in agent["do_not_touch"]


def test_acquire_unknown_session_lock_works_with_warning(isolated_home, capsys):
    rm.ensure_registry()  # registry esistente ma senza la sessione
    result = lm.acquire_lock("/tmp/sync-ghost.txt", "ghost")
    assert result["locked"] is True
    assert "ghost" in capsys.readouterr().err
    lm.release_lock("/tmp/sync-ghost.txt", "ghost")


def test_lock_dir_defaults_to_registry_home(isolated_home):
    assert lm._lock_dir() == isolated_home / "locks"


# --- 3.3: batch check ---


def test_batch_check_multiple_paths(isolated_home):
    rm.register_session("sid-1", "Kimi", "2.7", "Task")
    lm.acquire_lock("/tmp/batch-a.txt", "sid-1")

    results = lm.check_paths(["/tmp/batch-a.txt", "/tmp/batch-b.txt"], session_id="sid-1")
    by_path = {r["path"]: r for r in results}
    assert by_path["/tmp/batch-a.txt"]["state"] == "locked-by-me"
    assert by_path["/tmp/batch-b.txt"]["state"] == "free"

    results = lm.check_paths(["/tmp/batch-a.txt"], session_id="sid-2")
    entry = results[0]
    assert entry["state"] == "locked"
    assert entry["owner"] == "sid-1"
    assert entry["age"] >= 0


def test_batch_check_stale_lock(isolated_home):
    lm.acquire_lock("/tmp/batch-stale.txt", "sid-x")
    lm._lock_file("/tmp/batch-stale.txt").write_text("sid-x|0", encoding="utf-8")
    results = lm.check_paths(["/tmp/batch-stale.txt"])
    assert results[0]["state"] == "stale"
    assert results[0]["owner"] == "sid-x"


def test_cli_check_single_path_legacy_output(isolated_home, capsys):
    rm.register_session("sid-1", "Kimi", "2.7", "Task")
    lm.acquire_lock("/tmp/check-single.txt", "sid-1")
    capsys.readouterr()
    assert lm.main(["check", "/tmp/check-single.txt", "--session-id", "sid-2"]) == 0
    out = capsys.readouterr().out
    assert "locked" in out
    assert "sid-1" in out


def test_cli_check_batch_report(isolated_home, capsys):
    rm.register_session("sid-1", "Kimi", "2.7", "Task")
    lm.acquire_lock("/tmp/check-a.txt", "sid-1")
    capsys.readouterr()
    rc = lm.main(
        ["check", "/tmp/check-a.txt", "/tmp/check-b.txt", "--session-id", "sid-1"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "/tmp/check-a.txt: locked-by-me" in out
    assert "/tmp/check-b.txt: free" in out
