"""Test di concorrenza cross-process per agent-registry.

Verifica: openspec/specs/agent-registry/spec.md

Ogni scrittura avviene in un processo separato: è il modo in cui gli agenti
usano davvero il registry, e l'unico in cui la perdita di aggiornamenti
concorrenti è osservabile.
"""

from __future__ import annotations

from pathlib import Path

import pytest

RM = "registry_manager"
LM = "lock_manager"


# --- Requirement: Aggiornamenti concorrenti che non si perdono ---------------


def test_concurrent_registrations_are_all_preserved(runner):
    """Scenario: Registrazioni simultanee.

    Il difetto 0.1.0: register_session legge fuori dal lock, quindi tutti
    partono dalla stessa lista e l'ultimo che scrive vince.
    """
    n = 8
    calls = [
        (RM, "register_session", (f"agent-{i}", f"Provider{i}", "v1", f"task {i}"), {})
        for i in range(n)
    ]
    runner.race_varied(calls)

    agents = runner.call(RM, "load_agents")
    ids = sorted(a["session_id"] for a in agents)

    assert len(agents) == n, (
        f"Registrate {n} sessioni, sopravvissute {len(agents)}: {ids}. "
        "Read-modify-write non atomico: le registrazioni si sovrascrivono."
    )
    assert ids == sorted(f"agent-{i}" for i in range(n))


def test_concurrent_updates_are_all_preserved(runner):
    """Scenario: Aggiornamenti simultanei su sessioni diverse."""
    n = 8
    for i in range(n):
        runner.call(RM, "register_session", f"agent-{i}", f"P{i}", "v1", "iniziale")

    calls = [
        (RM, "update_session", (f"agent-{i}",), {"working_on": f"aggiornato-{i}"})
        for i in range(n)
    ]
    runner.race_varied(calls)

    agents = runner.call(RM, "load_agents")
    by_id = {a["session_id"]: a for a in agents}

    assert len(agents) == n, f"Sessioni perse durante gli update: {len(agents)}/{n}"
    for i in range(n):
        assert by_id[f"agent-{i}"]["working_on"] == f"aggiornato-{i}", (
            f"agent-{i} ha perso il proprio aggiornamento: {by_id[f'agent-{i}']}"
        )


def test_registry_stays_parsable_under_concurrent_writes(runner, isolated_env):
    """Scenario: Il registry resta sempre leggibile."""
    n = 10
    calls = [
        (RM, "register_session", (f"agent-{i}", f"P{i}", "v1", f"task {i}"), {})
        for i in range(n)
    ]
    runner.race_varied(calls)

    # Il parse deve riuscire: se la scrittura non fosse atomica troveremmo
    # un file troncato a metà.
    result = runner.call(RM, "parse_registry")
    assert "__exception__" not in result, f"Registry illeggibile dopo le scritture: {result}"

    raw = Path(isolated_env["AGENT_REGISTRY_PATH"]).read_text()
    assert raw.startswith("---"), "Frontmatter mancante: file troncato"
    assert raw.count("---") >= 2, "Frontmatter non chiuso: scrittura non atomica"


# --- Requirement: Chiusura di sessione con rilascio dei lock -----------------


def test_finish_releases_session_locks(runner, tmp_path):
    """Scenario: Chiusura rilascia i lock."""
    f = tmp_path / "auth.py"
    f.write_text("x")

    runner.call(RM, "register_session", "claude-1", "Claude", "4.8", "refactor")
    runner.call(LM, "acquire_lock", str(f), "claude-1")
    runner.call(RM, "update_session", "claude-1", do_not_touch=[str(f)])

    runner.call(RM, "unregister_session", "claude-1")

    agent = runner.call(RM, "find_agent", "claude-1")
    assert agent["status"] == "Finished"
    assert agent["do_not_touch"] == []

    taken = runner.call(LM, "acquire_lock", str(f), "kimi-2")
    assert taken["locked"] is True, (
        f"finish() non ha rilasciato il lock filesystem: {taken}. "
        "Registry e directory locks/ divergono."
    )


def test_finish_does_not_release_other_sessions_locks(runner, tmp_path):
    """Scenario: I lock altrui restano intatti."""
    mine = tmp_path / "mine.py"
    theirs = tmp_path / "theirs.py"
    mine.write_text("m")
    theirs.write_text("t")

    runner.call(RM, "register_session", "claude-1", "Claude", "4.8", "a")
    runner.call(RM, "register_session", "kimi-2", "Kimi", "2.7", "b")
    runner.call(LM, "acquire_lock", str(mine), "claude-1")
    runner.call(LM, "acquire_lock", str(theirs), "kimi-2")
    runner.call(RM, "update_session", "claude-1", do_not_touch=[str(mine), str(theirs)])

    runner.call(RM, "unregister_session", "claude-1")

    still = runner.call(LM, "is_locked", str(theirs))
    assert still["locked"] is True and still["session_id"] == "kimi-2", (
        f"La chiusura di claude-1 ha rilasciato il lock di kimi-2: {still}"
    )
