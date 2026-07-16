"""Test della CLI di lock_manager.

Verifica: openspec/specs/file-locking/spec.md — requisito
"Interfaccia a riga di comando con exit code significativi".

Un agente reagisce all'esito con `if ! python lock_manager.py acquire ...`.
Se il comando esce sempre 0, l'agente non può distinguere un lock ottenuto
da uno negato senza fare parsing del testo.
"""

from __future__ import annotations

SCRIPT = "lock_manager.py"


def test_acquire_free_path_exits_zero(runner, target_file):
    """Scenario: Acquisizione riuscita da CLI."""
    proc = runner.cli(SCRIPT, "acquire", str(target_file), "claude-1")
    assert proc.returncode == 0, f"stdout={proc.stdout} stderr={proc.stderr}"


def test_acquire_locked_path_exits_nonzero(runner, target_file):
    """Scenario: Acquisizione bloccata da CLI."""
    runner.cli(SCRIPT, "acquire", str(target_file), "claude-1")

    proc = runner.cli(SCRIPT, "acquire", str(target_file), "kimi-2")

    assert proc.returncode != 0, (
        "L'acquisizione bloccata e' uscita con 0: un agente in bash la "
        f"leggerebbe come successo. stdout={proc.stdout}"
    )
    assert "claude-1" in proc.stdout + proc.stderr, "L'owner corrente non e' riportato"


def test_release_by_non_owner_exits_nonzero(runner, target_file):
    proc_ok = runner.cli(SCRIPT, "acquire", str(target_file), "claude-1")
    assert proc_ok.returncode == 0

    proc = runner.cli(SCRIPT, "release", str(target_file), "kimi-2")
    assert proc.returncode != 0


def test_check_free_path_exits_zero(runner, target_file):
    proc = runner.cli(SCRIPT, "check", str(target_file), "claude-1")
    assert proc.returncode == 0


def test_check_locked_path_exits_nonzero(runner, target_file):
    runner.cli(SCRIPT, "acquire", str(target_file), "claude-1")

    proc = runner.cli(SCRIPT, "check", str(target_file), "kimi-2")

    assert proc.returncode != 0


def test_heartbeat_by_non_owner_exits_nonzero(runner, target_file):
    runner.cli(SCRIPT, "acquire", str(target_file), "claude-1")

    proc = runner.cli(SCRIPT, "heartbeat", str(target_file), "kimi-2")

    assert proc.returncode != 0


def test_missing_arguments_exit_nonzero_without_traceback(runner):
    """Scenario: Argomenti mancanti."""
    proc = runner.cli(SCRIPT, "acquire")

    assert proc.returncode != 0, "Comando incompleto uscito con 0"
    assert "Traceback" not in proc.stderr, (
        f"Traceback esposto invece dell'uso corretto:\n{proc.stderr}"
    )
    assert "acquire" in proc.stdout + proc.stderr, "L'uso corretto non e' stampato"


def test_unknown_command_exits_nonzero(runner):
    proc = runner.cli(SCRIPT, "frobnicate")
    assert proc.returncode != 0
