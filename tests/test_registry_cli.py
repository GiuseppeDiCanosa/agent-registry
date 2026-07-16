"""Test della CLI di registry_manager.

Verifica: openspec/specs/agent-registry/spec.md — requisito
"Interfaccia a riga di comando con exit code significativi".
"""

from __future__ import annotations

SCRIPT = "registry_manager.py"


def test_register_exits_zero_and_persists(runner):
    """Scenario: Registrazione da CLI."""
    proc = runner.cli(
        SCRIPT, "register", "claude-1", "Claude", "4.8", "refactor auth",
        "src/auth.py", "analisi",
    )
    assert proc.returncode == 0, f"stdout={proc.stdout} stderr={proc.stderr}"

    agents = runner.call("registry_manager", "load_agents")
    assert [a["session_id"] for a in agents] == ["claude-1"]


def test_update_unknown_session_exits_nonzero(runner):
    """Scenario: Aggiornamento di una sessione inesistente."""
    proc = runner.cli(SCRIPT, "update", "non-esiste", "qualcosa")

    assert proc.returncode != 0, (
        "L'update di una sessione inesistente ha riportato successo: "
        f"stdout={proc.stdout}"
    )


def test_finish_unknown_session_exits_nonzero(runner):
    proc = runner.cli(SCRIPT, "finish", "non-esiste")
    assert proc.returncode != 0


def test_handoff_unknown_session_exits_nonzero(runner):
    proc = runner.cli(SCRIPT, "handoff", "non-esiste", ".handoff/HANDOFF-001.md")
    assert proc.returncode != 0


def test_full_cli_lifecycle(runner):
    assert runner.cli(SCRIPT, "register", "claude-1", "Claude", "4.8", "lavoro").returncode == 0
    assert runner.cli(SCRIPT, "update", "claude-1", "altro lavoro").returncode == 0
    assert runner.cli(SCRIPT, "handoff", "claude-1", ".handoff/HANDOFF-007.md").returncode == 0
    assert runner.cli(SCRIPT, "show").returncode == 0
    assert runner.cli(SCRIPT, "finish", "claude-1").returncode == 0

    agent = runner.call("registry_manager", "find_agent", "claude-1")
    assert agent["status"] == "Finished"
    assert agent["handoff"] == ".handoff/HANDOFF-007.md"
    assert agent["working_on"] == "altro lavoro"


def test_missing_arguments_exit_nonzero_without_traceback(runner):
    """Scenario: Argomenti mancanti."""
    proc = runner.cli(SCRIPT, "register", "solo-un-id")

    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr, (
        f"Traceback esposto invece dell'uso corretto:\n{proc.stderr}"
    )
    assert "register" in proc.stdout + proc.stderr


def test_unknown_command_exits_nonzero(runner):
    proc = runner.cli(SCRIPT, "frobnicate")
    assert proc.returncode != 0
