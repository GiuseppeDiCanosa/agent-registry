"""Test cross-process per file-locking.

Verifica: openspec/specs/file-locking/spec.md

Ogni acquisizione avviene in un processo che termina prima dell'asserzione:
è il modello d'esecuzione reale degli agenti CLI, e l'unico in cui i difetti
della 0.1.0 sono osservabili.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

LM = "lock_manager"


# --- Requirement: Mutua esclusione fra processi one-shot ---------------------


def test_second_agent_cannot_acquire_valid_lock(runner, target_file):
    """Scenario: Un secondo agente non può acquisire un lock valido.

    È il furto del lock della 0.1.0: A acquisisce in un processo che muore,
    B acquisisce 'con successo' lo stesso path pochi istanti dopo.
    """
    a = runner.call(LM, "acquire_lock", str(target_file), "claude-111")
    assert a["locked"] is True, f"A doveva acquisire: {a}"

    b = runner.call(LM, "acquire_lock", str(target_file), "kimi-222")

    assert b["locked"] is False, (
        f"B ha rubato il lock di A. Risultato: {b}. "
        "Il lock non sopravvive alla morte del processo acquirente."
    )
    assert b.get("session_id") == "claude-111", (
        f"B doveva vedere claude-111 come owner, ha visto: {b.get('session_id')}"
    )


def test_owner_keeps_lock_after_failed_takeover(runner, target_file):
    """Scenario: L'owner conserva il proprio lock."""
    runner.call(LM, "acquire_lock", str(target_file), "claude-111")
    runner.call(LM, "acquire_lock", str(target_file), "kimi-222")

    status = runner.call(LM, "is_locked", str(target_file))

    assert status["locked"] is True
    assert status["session_id"] == "claude-111", (
        f"A ha perso il lock senza saperlo: owner ora e' {status['session_id']}"
    )


def test_concurrent_acquire_elects_single_winner(runner, target_file):
    """Scenario: Acquisizioni concorrenti in massa eleggono un solo vincitore."""
    n = 8
    calls = [
        (LM, "acquire_lock", (str(target_file), f"agent-{i}"), {}) for i in range(n)
    ]
    results = runner.race_varied(calls)

    winners = [r for r in results if r and r.get("locked") is True]
    assert len(winners) == 1, (
        f"Attesi 1 vincitore su {n}, trovati {len(winners)}: "
        f"{[w.get('owner') for w in winners]}"
    )

    status = runner.call(LM, "is_locked", str(target_file))
    assert status["locked"] is True
    assert status["session_id"] == winners[0]["owner"], (
        "L'owner su disco non coincide con il vincitore dichiarato"
    )


def test_distinct_paths_do_not_interfere(runner, tmp_path):
    """Scenario: Path distinti non interferiscono."""
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    f1.write_text("a")
    f2.write_text("b")

    r1 = runner.call(LM, "acquire_lock", str(f1), "agent-1")
    r2 = runner.call(LM, "acquire_lock", str(f2), "agent-2")

    assert r1["locked"] is True
    assert r2["locked"] is True


# --- Requirement: Riacquisizione idempotente da parte dell'owner -------------


def test_owner_reacquire_is_idempotent(runner, target_file):
    """Scenario: L'owner riacquisisce da un nuovo processo."""
    runner.call(LM, "acquire_lock", str(target_file), "claude-111")

    again = runner.call(LM, "acquire_lock", str(target_file), "claude-111")

    assert again["locked"] is True, (
        f"L'owner si e' autoescluso dal proprio lock: {again}"
    )
    status = runner.call(LM, "is_locked", str(target_file))
    assert status["session_id"] == "claude-111"


# --- Requirement: Scadenza dei lock abbandonati ------------------------------


@pytest.mark.slow
def test_stale_lock_is_detected(runner, target_file):
    """Scenario: Un lock scaduto viene rilevato."""
    runner.call(LM, "acquire_lock", str(target_file), "crashed-agent", timeout=0.3)
    time.sleep(0.5)

    status = runner.call(LM, "is_locked", str(target_file), timeout=0.3)

    assert status["locked"] is False
    assert status.get("stale_owner") == "crashed-agent"


@pytest.mark.slow
def test_stale_lock_can_be_acquired(runner, target_file):
    """Scenario: Un lock scaduto può essere acquisito."""
    runner.call(LM, "acquire_lock", str(target_file), "crashed-agent", timeout=0.3)
    time.sleep(0.5)

    result = runner.call(LM, "acquire_lock", str(target_file), "fresh-agent", timeout=0.3)

    assert result["locked"] is True
    assert result["owner"] == "fresh-agent"
    assert result.get("stale_owner") == "crashed-agent"


@pytest.mark.slow
def test_stale_takeover_race_has_single_winner(runner, target_file):
    """Scenario: Corsa su un lock stale.

    Il difetto 0.1.0: is_locked() faceva unlink() dello stale e tornava
    'libero', quindi N agenti che lo osservano insieme lo prendono tutti.
    """
    runner.call(LM, "acquire_lock", str(target_file), "crashed-agent", timeout=0.3)
    time.sleep(0.5)

    n = 6
    calls = [
        (LM, "acquire_lock", (str(target_file), f"taker-{i}"), {"timeout": 0.3})
        for i in range(n)
    ]
    results = runner.race_varied(calls)

    winners = [r for r in results if r and r.get("locked") is True]
    assert len(winners) == 1, (
        f"Il takeover di uno stale ha eletto {len(winners)} vincitori su {n}: "
        f"{[w.get('owner') for w in winners]}"
    )


# --- Requirement: Rinnovo riservato all'owner --------------------------------


def test_owner_can_heartbeat(runner, target_file):
    """Scenario: L'owner rinnova."""
    runner.call(LM, "acquire_lock", str(target_file), "claude-111")
    time.sleep(0.2)

    hb = runner.call(LM, "heartbeat", str(target_file), "claude-111")

    assert hb["ok"] is True
    status = runner.call(LM, "is_locked", str(target_file))
    assert status["age"] < 0.2, f"L'eta' non e' ripartita: {status['age']}"


def test_non_owner_cannot_heartbeat(runner, target_file):
    """Scenario: Un non-owner tenta il rinnovo."""
    runner.call(LM, "acquire_lock", str(target_file), "claude-111")

    hb = runner.call(LM, "heartbeat", str(target_file), "kimi-222")

    assert hb["ok"] is False
    assert hb["error"] == "not owner"
    assert hb.get("current_owner") == "claude-111"


def test_heartbeat_on_missing_lock_fails(runner, target_file):
    """Scenario: Heartbeat su lock assente."""
    hb = runner.call(LM, "heartbeat", str(target_file), "claude-111")

    assert hb["ok"] is False
    status = runner.call(LM, "is_locked", str(target_file))
    assert status["locked"] is False, "L'heartbeat ha creato un lock dal nulla"


@pytest.mark.slow
def test_heartbeat_prevents_expiry(runner, target_file):
    """Scenario: Il rinnovo previene la scadenza."""
    runner.call(LM, "acquire_lock", str(target_file), "claude-111", timeout=0.6)

    for _ in range(4):
        time.sleep(0.2)
        hb = runner.call(LM, "heartbeat", str(target_file), "claude-111")
        assert hb["ok"] is True

    other = runner.call(LM, "acquire_lock", str(target_file), "kimi-222", timeout=0.6)
    assert other["locked"] is False, "Un lock rinnovato e' stato comunque rubato"


# --- Requirement: Rilascio riservato all'owner -------------------------------


def test_owner_can_release(runner, target_file):
    """Scenario: L'owner rilascia."""
    runner.call(LM, "acquire_lock", str(target_file), "claude-111")

    rel = runner.call(LM, "release_lock", str(target_file), "claude-111")
    assert rel["released"] is True

    after = runner.call(LM, "acquire_lock", str(target_file), "kimi-222")
    assert after["locked"] is True, "Il path non e' tornato acquisibile dopo il rilascio"


def test_non_owner_cannot_release(runner, target_file):
    """Scenario: Un non-owner tenta il rilascio."""
    runner.call(LM, "acquire_lock", str(target_file), "claude-111")

    rel = runner.call(LM, "release_lock", str(target_file), "kimi-222")

    assert rel["released"] is False
    assert rel["error"] == "not owner"
    assert rel.get("current_owner") == "claude-111"

    status = runner.call(LM, "is_locked", str(target_file))
    assert status["locked"] is True and status["session_id"] == "claude-111"


def test_release_missing_lock_is_noop(runner, target_file):
    """Scenario: Rilascio di un lock inesistente."""
    rel = runner.call(LM, "release_lock", str(target_file), "claude-111")
    assert "__exception__" not in rel, f"Eccezione sul rilascio a vuoto: {rel}"
    assert rel["released"] is True


# --- Requirement: Identità del lock indipendente dalla cwd -------------------


def test_relative_and_absolute_path_are_same_lock(runner, target_file):
    """Scenario: Path relativo e assoluto sono lo stesso lock."""
    rel = runner.call(
        LM, "acquire_lock", "auth.py", "agent-rel", cwd=str(target_file.parent)
    )
    assert rel["locked"] is True, f"Acquisizione con path relativo fallita: {rel}"

    abs_result = runner.call(LM, "acquire_lock", str(target_file), "agent-abs")

    assert abs_result["locked"] is False, (
        "Path relativo e assoluto hanno prodotto due lock distinti sullo stesso file"
    )


def test_same_name_different_projects_do_not_collide(runner, tmp_path):
    """Scenario: File omonimi in progetti diversi."""
    p1 = tmp_path / "proj1" / "src"
    p2 = tmp_path / "proj2" / "src"
    p1.mkdir(parents=True)
    p2.mkdir(parents=True)
    (p1 / "auth.py").write_text("1")
    (p2 / "auth.py").write_text("2")

    r1 = runner.call(LM, "acquire_lock", str(p1 / "auth.py"), "agent-1")
    r2 = runner.call(LM, "acquire_lock", str(p2 / "auth.py"), "agent-2")

    assert r1["locked"] is True
    assert r2["locked"] is True, "File omonimi in progetti diversi si sono bloccati"


# --- Requirement: Directory dei lock configurabile ---------------------------


def test_lock_dir_env_override_is_respected(runner, isolated_env, fake_home, target_file):
    """Scenario: Override via ambiente."""
    runner.call(LM, "acquire_lock", str(target_file), "agent-1")

    lock_dir = Path(isolated_env["AGENT_REGISTRY_LOCK_DIR"])
    locks = list(lock_dir.glob("*.lock")) if lock_dir.exists() else []

    assert locks, (
        f"Nessun lock in {lock_dir}: AGENT_REGISTRY_LOCK_DIR ignorato. "
        "Il default e' probabilmente risolto all'import invece che a ogni chiamata."
    )

    fallback = fake_home / "Desktop" / "agent-registry" / "locks"
    assert not list(fallback.glob("*.lock")), (
        f"Lock scritto nel default ({fallback}) invece che nell'override: "
        "senza HOME finta questo avrebbe sporcato il Desktop dell'utente."
    )


def test_lock_dir_is_created_when_missing(runner, isolated_env, target_file):
    """Scenario: Directory assente."""
    lock_dir = Path(isolated_env["AGENT_REGISTRY_LOCK_DIR"])
    assert not lock_dir.exists()

    result = runner.call(LM, "acquire_lock", str(target_file), "agent-1")

    assert result["locked"] is True
    assert lock_dir.exists(), "La directory dei lock non e' stata creata"
