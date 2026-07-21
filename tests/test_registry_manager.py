"""Test per registry_manager.py."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import registry_manager as rm


@pytest.fixture(autouse=True)
def _no_real_legacy(monkeypatch, tmp_path):
    """Impedisce che i test tocchino un eventuale registry legacy reale su Desktop."""
    monkeypatch.setattr(
        rm, "_legacy_registry_path", lambda: tmp_path / "no-legacy" / "registry.md"
    )


@pytest.fixture
def tmp_registry(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "registry.md"
        monkeypatch.setenv("AGENT_REGISTRY_PATH", str(path))
        yield path


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Home nuova (AGENT_REGISTRY_HOME) in una directory temporanea inesistente."""
    home = tmp_path / "registry-home"
    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(home))
    monkeypatch.delenv("AGENT_REGISTRY_PATH", raising=False)
    return home


def test_ensure_registry_creates_file(tmp_registry):
    path = rm.ensure_registry()
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "version: \"1.0\"" in content
    assert "agents: []" in content


def test_register_and_load(tmp_registry):
    agent = rm.register_session(
        session_id="sid-1",
        provider="Kimi",
        ai_version="2.7",
        working_on="Test",
        space=["a.py"],
        todo_present=["t1", "t2"],
    )
    assert agent["session_id"] == "sid-1"
    assert agent["status"] == "OnWorking"

    agents = rm.load_agents()
    assert len(agents) == 1
    assert agents[0]["provider"] == "Kimi"
    assert agents[0]["todo"]["present"] == ["t1", "t2"]


def test_update_session(tmp_registry):
    rm.register_session("sid-1", "Kimi", "2.7", "Test", ["a.py"], ["t1"])
    updated = rm.update_session(
        "sid-1",
        working_on="Updated",
        todo={"past": ["t1"], "present": ["t2"], "future": ["t3"]},
        issues="nessuno",
    )
    assert updated is not None
    assert updated["working_on"] == "Updated"
    assert updated["todo"]["past"] == ["t1"]
    assert updated["issues"] == "nessuno"


def test_unregister_session(tmp_registry):
    rm.register_session("sid-1", "Kimi", "2.7", "Test", ["a.py"], ["t1"])
    finished = rm.unregister_session("sid-1")
    assert finished["status"] == "Finished"
    assert finished["do_not_touch"] == []


def test_add_handoff_ref(tmp_registry):
    rm.register_session("sid-1", "Kimi", "2.7", "Test", ["a.py"], ["t1"])
    agent = rm.add_handoff_ref("sid-1", ".handoff-kimi/HANDOFF-001.md")
    assert agent["handoff"] == ".handoff-kimi/HANDOFF-001.md"


def test_reregister_same_session_id_replaces(tmp_registry):
    rm.register_session("sid-1", "Kimi", "2.7", "First", ["a.py"], ["t1"])
    rm.register_session("sid-1", "Claude", "4.8", "Second", ["b.py"], ["t2"])
    agents = rm.load_agents()
    assert len(agents) == 1
    assert agents[0]["provider"] == "Claude"


def test_find_agent(tmp_registry):
    rm.register_session("sid-1", "Kimi", "2.7", "Test", ["a.py"], ["t1"])
    found = rm.find_agent("sid-1")
    assert found is not None
    assert found["provider"] == "Kimi"
    assert rm.find_agent("missing") is None


# --- Gruppo 1: home, storage per-sessione, migrazione, cattura automatica ---


def test_get_registry_home_env_override(tmp_home):
    assert rm.get_registry_home() == tmp_home
    assert rm.get_registry_path() == tmp_home / "registry.md"


def test_registry_path_alias_deprecated(tmp_path, monkeypatch):
    fake = tmp_path / "custom" / "registry.md"
    monkeypatch.delenv("AGENT_REGISTRY_HOME", raising=False)
    monkeypatch.setenv("AGENT_REGISTRY_PATH", str(fake))
    with pytest.warns(DeprecationWarning):
        home = rm.get_registry_home()
    assert home == fake.parent
    assert rm.get_registry_path() == fake


def test_ensure_registry_creates_structure(tmp_home):
    path = rm.ensure_registry()
    assert path == tmp_home / "registry.md"
    assert path.exists()
    for sub in ("sessions", "contexts", "locks", "wiki"):
        assert (tmp_home / sub).is_dir()
    content = path.read_text(encoding="utf-8")
    assert 'version: "1.0"' in content
    assert "agents: []" in content


def test_session_file_is_source_and_view_regenerated(tmp_home):
    agent = rm.register_session(
        session_id="sid-1",
        provider="Kimi",
        ai_version="2.7",
        working_on="Test",
        space=["a.py"],
        todo_present=["t1"],
    )
    # Fonte di verità: file sessione YAML
    session_file = tmp_home / "sessions" / "sid-1.yaml"
    assert session_file.exists()
    data = yaml.safe_load(session_file.read_text(encoding="utf-8"))
    assert data["session_id"] == "sid-1"
    assert data["status"] == "OnWorking"
    assert data["todo"]["present"] == ["t1"]

    # Vista rigenerata: frontmatter con agents + tabella markdown
    view = (tmp_home / "registry.md").read_text(encoding="utf-8")
    frontmatter, body = rm.parse_registry(tmp_home / "registry.md")
    assert len(frontmatter["agents"]) == 1
    assert "| Session ID |" in body
    assert "| sid-1 | Kimi |" in view


def test_update_session_updates_file_and_view(tmp_home):
    rm.register_session("sid-1", "Kimi", "2.7", "Test", ["a.py"], ["t1"])
    rm.update_session("sid-1", working_on="Updated", issues="bug X")
    data = yaml.safe_load((tmp_home / "sessions" / "sid-1.yaml").read_text(encoding="utf-8"))
    assert data["working_on"] == "Updated"
    assert data["issues"] == "bug X"
    frontmatter, _ = rm.parse_registry(tmp_home / "registry.md")
    assert frontmatter["agents"][0]["issues"] == "bug X"


def test_unregister_and_find_via_session_files(tmp_home):
    rm.register_session("sid-1", "Kimi", "2.7", "Test", ["a.py"], ["t1"])
    finished = rm.unregister_session("sid-1")
    assert finished["status"] == "Finished"
    assert rm.find_agent("sid-1")["status"] == "Finished"
    assert rm.find_agent("missing") is None


def test_reregister_replaces_session_file(tmp_home):
    rm.register_session("sid-1", "Kimi", "2.7", "First", ["a.py"], ["t1"])
    rm.register_session("sid-1", "Claude", "4.8", "Second", ["b.py"], ["t2"])
    assert len(list((tmp_home / "sessions").glob("*.yaml"))) == 1
    agents = rm.load_agents()
    assert len(agents) == 1
    assert agents[0]["provider"] == "Claude"


def test_save_agents_removes_missing_sessions(tmp_home):
    rm.register_session("sid-1", "Kimi", "2.7", "A")
    rm.register_session("sid-2", "Claude", "4.8", "B")
    rm.save_agents([{"session_id": "sid-2", "provider": "Claude", "status": "OnWorking"}])
    files = sorted(f.name for f in (tmp_home / "sessions").glob("*.yaml"))
    assert files == ["sid-2.yaml"]
    agents = rm.load_agents()
    assert [a["session_id"] for a in agents] == ["sid-2"]


def _write_legacy_registry(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    agents = [
        {
            "session_id": "old-1",
            "provider": "Kimi",
            "ai_version": "2.7",
            "started_at": "2026-07-01 10:00",
            "working_on": "Legacy task",
            "todo": {"past": [], "present": ["x"], "future": []},
            "space": ["a.py"],
            "do_not_touch": [],
            "status": "OnWorking",
            "issues": "",
            "handoff": "",
        },
        {
            "session_id": "old-2",
            "provider": "Claude",
            "ai_version": "4.8",
            "started_at": "2026-07-01 11:00",
            "working_on": "Altro",
            "todo": {"past": [], "present": [], "future": []},
            "space": [],
            "do_not_touch": [],
            "status": "Finished",
            "issues": "",
            "handoff": ".handoff/H1.md",
        },
    ]
    path.write_text(rm._dump_registry(agents), encoding="utf-8")


def test_migration_from_legacy(tmp_home, tmp_path, monkeypatch, capsys):
    legacy = tmp_path / "Desktop" / "agent-registry" / "registry.md"
    _write_legacy_registry(legacy)
    monkeypatch.setattr(rm, "_legacy_registry_path", lambda: legacy)

    assert not tmp_home.exists()
    rm.ensure_registry()

    # Dati migrati in formato per-sessione
    assert (tmp_home / "sessions" / "old-1.yaml").exists()
    assert (tmp_home / "sessions" / "old-2.yaml").exists()
    agents = rm.load_agents()
    assert {a["session_id"] for a in agents} == {"old-1", "old-2"}
    assert rm.find_agent("old-1")["working_on"] == "Legacy task"

    # Avviso stampato e vecchio file marcato DEPRECATO
    assert "migrato" in capsys.readouterr().out.lower()
    legacy_text = legacy.read_text(encoding="utf-8")
    assert legacy_text.startswith("> **DEPRECATO**")


def test_migration_skipped_if_home_exists(tmp_home, tmp_path, monkeypatch):
    tmp_home.mkdir(parents=True)
    legacy = tmp_path / "Desktop" / "agent-registry" / "registry.md"
    _write_legacy_registry(legacy)
    monkeypatch.setattr(rm, "_legacy_registry_path", lambda: legacy)

    migrated = rm.migrate_from_legacy()
    assert migrated is False
    assert not legacy.read_text(encoding="utf-8").startswith("> **DEPRECATO**")
    assert not (tmp_home / "sessions" / "old-1.yaml").exists()


def test_register_captures_pid_cmdline_project(tmp_home, monkeypatch, tmp_path):
    workdir = tmp_path / "mio-progetto"
    workdir.mkdir()
    monkeypatch.chdir(workdir)  # non un repo git
    agent = rm.register_session("sid-1", "Kimi", "2.7", "Test")
    assert agent["pid"] == os.getpid()
    assert agent["cmdline"]
    assert agent["project"] == "mio-progetto"
    assert agent["git_branch"] == ""  # tollerante a non-git


@pytest.mark.skipif(shutil.which("git") is None, reason="git non disponibile")
def test_git_branch_captured_in_repo(tmp_home, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    git("init")
    (repo / "f.txt").write_text("x")
    git("add", ".")
    git("commit", "-m", "init")
    expected = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    monkeypatch.chdir(repo)
    agent = rm.register_session("sid-1", "Kimi", "2.7", "Test")
    assert agent["git_branch"] == expected
    assert agent["project"] == "repo"
