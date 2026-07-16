"""Test per registry_manager.py."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import registry_manager as rm


@pytest.fixture
def tmp_registry(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "registry.md"
        monkeypatch.setenv("AGENT_REGISTRY_PATH", str(path))
        yield path


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
