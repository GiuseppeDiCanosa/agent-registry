"""Test per la CLI argparse di registry_manager (gruppo 3): update, status, cleanup, kill."""

import os
import subprocess
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


# --- 3.1: CLI argparse ---


def test_cli_register_with_flags(isolated_home):
    rc = rm.main(
        ["register", "sid-1", "Kimi", "2.7", "Task", "--space", "a.py, b.py",
         "--todo-present", "t1,t2"]
    )
    assert rc == 0
    agent = rm.find_agent("sid-1")
    assert agent["space"] == ["a.py", "b.py"]
    assert agent["todo"]["present"] == ["t1", "t2"]


def test_cli_update_all_fields(isolated_home):
    assert rm.main(["register", "sid-1", "Kimi", "2.7", "Task iniziale"]) == 0
    rc = rm.main(
        ["update", "sid-1",
         "--working-on", "Nuovo task",
         "--todo-past", "t1,t2",
         "--todo-present", "t3",
         "--todo-future", "t4, t5",
         "--space", "a.py,b.py",
         "--do-not-touch", "c.py",
         "--issues", "bug X",
         "--status", "OnWorking"]
    )
    assert rc == 0
    agent = rm.find_agent("sid-1")
    assert agent["working_on"] == "Nuovo task"
    assert agent["todo"] == {
        "past": ["t1", "t2"],
        "present": ["t3"],
        "future": ["t4", "t5"],
    }
    assert agent["space"] == ["a.py", "b.py"]
    assert agent["do_not_touch"] == ["c.py"]
    assert agent["issues"] == "bug X"
    assert agent["status"] == "OnWorking"


def test_cli_update_missing_session_returns_1(isolated_home, capsys):
    rc = rm.main(["update", "ghost", "--working-on", "X"])
    assert rc == 1
    assert "non trovata" in capsys.readouterr().err


def test_cli_finish_and_handoff(isolated_home):
    rm.main(["register", "sid-1", "Kimi", "2.7", "Task"])
    assert rm.main(["handoff", "sid-1", ".handoff/H1.md"]) == 0
    assert rm.find_agent("sid-1")["handoff"] == ".handoff/H1.md"
    assert rm.main(["finish", "sid-1"]) == 0
    assert rm.find_agent("sid-1")["status"] == "Finished"


def test_cli_status_table_and_filters(isolated_home, capsys):
    rm.main(["register", "sid-1", "Kimi", "2.7", "Task uno"])
    rm.main(["register", "sid-2", "Claude", "4.8", "Task due"])
    capsys.readouterr()

    assert rm.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "SESSION ID" in out and "PROVIDER" in out and "ETÀ" in out
    assert "sid-1" in out and "sid-2" in out
    assert "git-sync:" in out

    assert rm.main(["status", "--provider", "Claude"]) == 0
    out = capsys.readouterr().out
    assert "sid-2" in out and "sid-1" not in out

    assert rm.main(["status", "--status", "Finished"]) == 0
    out = capsys.readouterr().out
    assert "(nessuna sessione)" in out


def test_status_sorted_by_started_at_desc(isolated_home):
    rm.register_session("sid-old", "Kimi", "2.7", "Vecchio")
    rm.update_session("sid-old", started_at="2020-01-01 10:00")
    rm.register_session("sid-new", "Claude", "4.8", "Nuovo")
    table = rm.render_status()
    assert table.index("sid-new") < table.index("sid-old")


# --- 3.4: cleanup ---


def test_cleanup_marks_dead_pid_session_stop(isolated_home, capsys):
    rm.register_session("sid-dead", "Kimi", "2.7", "Task")
    rm.update_session("sid-dead", pid=999999)  # pid sicuramente inesistente
    cleaned = rm.cleanup_sessions()
    assert cleaned == ["sid-dead"]
    assert rm.find_agent("sid-dead")["status"] == "Stop"


def test_cleanup_leaves_alive_session_with_fresh_lock(isolated_home):
    rm.register_session("sid-alive", "Kimi", "2.7", "Task")  # pid = os.getpid()
    lm.acquire_lock("/tmp/cleanup-alive.txt", "sid-alive")
    cleaned = rm.cleanup_sessions()
    assert cleaned == []
    assert rm.find_agent("sid-alive")["status"] == "OnWorking"
    assert lm.is_locked("/tmp/cleanup-alive.txt")["locked"] is True
    lm.release_lock("/tmp/cleanup-alive.txt", "sid-alive")


def test_cleanup_releases_stale_locks(isolated_home):
    rm.register_session("sid-stale", "Kimi", "2.7", "Task")
    lm.acquire_lock("/tmp/cleanup-stale.txt", "sid-stale")
    # Lock stale: timestamp azzerato nel file
    lm._lock_file("/tmp/cleanup-stale.txt").write_text("sid-stale|0", encoding="utf-8")
    cleaned = rm.cleanup_sessions()
    assert cleaned == ["sid-stale"]
    agent = rm.find_agent("sid-stale")
    assert agent["status"] == "Stop"
    assert agent["do_not_touch"] == []
    assert lm.is_locked("/tmp/cleanup-stale.txt")["locked"] is False


def test_cleanup_ignores_finished_sessions(isolated_home):
    rm.register_session("sid-fin", "Kimi", "2.7", "Task")
    rm.unregister_session("sid-fin")
    rm.update_session("sid-fin", pid=999999)
    assert rm.cleanup_sessions() == []
    assert rm.find_agent("sid-fin")["status"] == "Finished"


def test_cli_cleanup_prints_count(isolated_home, capsys):
    rm.register_session("sid-dead", "Kimi", "2.7", "Task")
    rm.update_session("sid-dead", pid=999999)
    capsys.readouterr()
    assert rm.main(["cleanup"]) == 0
    out = capsys.readouterr().out
    assert "1 sessioni zombie" in out
    assert "sid-dead" in out


# --- 3.5: kill ---


def test_kill_real_process_sigterm(isolated_home):
    proc = subprocess.Popen(["sleep", "30"])
    try:
        rm.register_session("sid-kill", "Kimi", "2.7", "Task")
        rm.update_session("sid-kill", pid=proc.pid, cmdline="sleep 30")
        result = rm.kill_session("sid-kill")
        assert result["killed"] is True
        assert result["terminated"] is True
        assert rm.find_agent("sid-kill")["status"] == "Killed"
        proc.wait(timeout=5)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_kill_nonexistent_pid_logical_stop(isolated_home):
    rm.register_session("sid-ghost", "Kimi", "2.7", "Task")
    rm.update_session("sid-ghost", pid=999999, cmdline="sleep 30")
    result = rm.kill_session("sid-ghost")
    assert result["killed"] is True
    assert result["terminated"] is False
    assert "non terminato" in result["note"]
    assert rm.find_agent("sid-ghost")["status"] == "Killed"


def test_kill_rejects_reused_pid(isolated_home):
    """PID vivo ma cmdline incompatibile (PID riusato): niente kill, stop logico."""
    proc = subprocess.Popen(["sleep", "30"])
    try:
        rm.register_session("sid-reused", "Kimi", "2.7", "Task")
        rm.update_session("sid-reused", pid=proc.pid, cmdline="python agente.py --sid sid-reused")
        result = rm.kill_session("sid-reused")
        assert result["killed"] is True
        assert result["terminated"] is False
        assert "non terminato" in result["note"]
        assert proc.poll() is None  # processo innocente ancora vivo
        assert rm.find_agent("sid-reused")["status"] == "Killed"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_kill_force_skips_cmdline_check(isolated_home):
    proc = subprocess.Popen(["sleep", "30"])
    try:
        rm.register_session("sid-force", "Kimi", "2.7", "Task")
        rm.update_session("sid-force", pid=proc.pid, cmdline="tutto-un-altro-comando")
        result = rm.kill_session("sid-force", force=True)
        assert result["terminated"] is True
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_kill_releases_locks(isolated_home):
    rm.register_session("sid-locks", "Kimi", "2.7", "Task")
    lm.acquire_lock("/tmp/kill-lock.txt", "sid-locks")
    rm.update_session("sid-locks", pid=999999)
    result = rm.kill_session("sid-locks")
    assert result["killed"] is True
    agent = rm.find_agent("sid-locks")
    assert agent["status"] == "Killed"
    assert agent["do_not_touch"] == []
    assert lm.is_locked("/tmp/kill-lock.txt")["locked"] is False


def test_kill_missing_session(isolated_home):
    result = rm.kill_session("ghost")
    assert result["killed"] is False
    assert "non trovata" in result["error"]


def test_cli_kill(isolated_home, capsys):
    rm.register_session("sid-cli", "Kimi", "2.7", "Task")
    rm.update_session("sid-cli", pid=999999)
    capsys.readouterr()
    assert rm.main(["kill", "sid-cli"]) == 0
    out = capsys.readouterr().out
    assert "Killed" in out
    assert rm.find_agent("sid-cli")["status"] == "Killed"


def test_pid_alive_helpers():
    assert rm._pid_alive(os.getpid()) is True
    assert rm._pid_alive(999999) is False
    assert rm._pid_alive(None) is False
    assert rm._pid_alive(-1) is False
