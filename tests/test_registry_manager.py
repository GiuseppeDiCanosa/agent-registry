"""Test unitari per agent-registry.

Verifica: openspec/specs/agent-registry/spec.md

Anche questi girano in processi separati: il registry è per definizione uno
stato condiviso fra processi, testarlo in-process ne verificherebbe una
versione che nella realtà non esiste.
"""

from __future__ import annotations

from pathlib import Path

RM = "registry_manager"


# --- Requirement: Registrazione di una sessione agente -----------------------


def test_register_new_session(runner):
    """Scenario: Nuova sessione."""
    agent = runner.call(
        RM, "register_session", "claude-1", "Claude", "4.8", "refactor auth",
        space=["src/auth.py"], todo_present=["analisi"],
    )

    assert agent["session_id"] == "claude-1"
    assert agent["status"] == "OnWorking"
    assert agent["provider"] == "Claude"
    assert agent["space"] == ["src/auth.py"]
    assert agent["todo"]["present"] == ["analisi"]
    assert agent["started_at"]


def test_reregister_same_id_replaces(runner):
    """Scenario: Re-registrazione dello stesso id."""
    runner.call(RM, "register_session", "claude-1", "Kimi", "2.7", "primo")
    runner.call(RM, "register_session", "claude-1", "Claude", "4.8", "secondo")

    agents = runner.call(RM, "load_agents")

    assert len(agents) == 1, f"Sessione duplicata: {agents}"
    assert agents[0]["working_on"] == "secondo"
    assert agents[0]["provider"] == "Claude"


# --- Requirement: Aggiornamento dei campi di sessione ------------------------


def test_partial_update_preserves_other_fields(runner):
    """Scenario: Aggiornamento parziale."""
    runner.call(
        RM, "register_session", "claude-1", "Claude", "4.8", "iniziale",
        space=["src/auth.py"], todo_present=["analisi"],
    )

    runner.call(RM, "update_session", "claude-1", working_on="nuovo lavoro")

    agent = runner.call(RM, "find_agent", "claude-1")
    assert agent["working_on"] == "nuovo lavoro"
    assert agent["space"] == ["src/auth.py"], "space azzerato da un update parziale"
    assert agent["provider"] == "Claude"
    assert agent["todo"]["present"] == ["analisi"]


def test_update_unknown_session_does_not_create_it(runner):
    """Scenario: Sessione inesistente."""
    result = runner.call(RM, "update_session", "non-esiste", working_on="x")

    assert result is None, (
        f"L'update ha restituito un valore per una sessione assente: {result}"
    )
    agents = runner.call(RM, "load_agents")
    assert agents == [], f"L'update ha creato implicitamente la sessione: {agents}"


def test_update_todo_merges_subfields(runner):
    runner.call(RM, "register_session", "claude-1", "Claude", "4.8", "x", todo_present=["a"])

    runner.call(RM, "update_session", "claude-1", todo={"future": ["b"]})

    agent = runner.call(RM, "find_agent", "claude-1")
    assert agent["todo"]["present"] == ["a"], "todo.present perso durante il merge"
    assert agent["todo"]["future"] == ["b"]


def test_unregister_marks_finished(runner):
    runner.call(RM, "register_session", "claude-1", "Claude", "4.8", "x")

    finished = runner.call(RM, "unregister_session", "claude-1")

    assert finished["status"] == "Finished"
    assert finished["do_not_touch"] == []


# --- Requirement: Percorso del registry configurabile ------------------------


def test_registry_path_env_override(runner, isolated_env, fake_home):
    """Scenario: Override via ambiente."""
    registry = Path(isolated_env["AGENT_REGISTRY_PATH"])
    assert not registry.exists()

    runner.call(RM, "register_session", "claude-1", "Claude", "4.8", "x")

    assert registry.exists(), f"Registry non creato in {registry}: override ignorato"
    assert "claude-1" in registry.read_text()

    fallback = fake_home / "Desktop" / "agent-registry" / "registry.md"
    assert not fallback.exists(), (
        "Registry scritto nel default invece che nell'override: "
        "senza HOME finta avrebbe sporcato il Desktop dell'utente."
    )


def test_registry_parent_dirs_are_created(runner, tmp_path, isolated_env):
    from conftest import ProcessRunner

    nested = tmp_path / "a" / "b" / "c" / "registry.md"
    env = dict(isolated_env)
    env["AGENT_REGISTRY_PATH"] = str(nested)

    ProcessRunner(env).call(RM, "register_session", "claude-1", "Claude", "4.8", "x")

    assert nested.exists(), "Le directory intermedie non sono state create"


def test_ensure_registry_creates_valid_skeleton(runner, isolated_env):
    runner.call(RM, "ensure_registry")

    raw = Path(isolated_env["AGENT_REGISTRY_PATH"]).read_text()
    assert raw.startswith("---")
    assert "agents:" in raw

    agents = runner.call(RM, "load_agents")
    assert agents == []


# --- Requirement: Registry leggibile da umani e da macchine ------------------


def test_table_matches_frontmatter(runner, isolated_env):
    """Scenario: Tabella coerente col frontmatter."""
    for i in range(3):
        runner.call(RM, "register_session", f"agent-{i}", f"P{i}", "v1", f"task {i}")

    raw = Path(isolated_env["AGENT_REGISTRY_PATH"]).read_text()
    body = raw.split("---", 2)[2]
    rows = [l for l in body.splitlines() if l.strip().startswith("|")]

    # header + separatore + 3 agenti
    assert len(rows) == 5, f"Righe tabella inattese: {rows}"
    for i in range(3):
        assert f"agent-{i}" in body


def test_pipe_and_newline_are_escaped_in_scalars(runner, isolated_env):
    """Scenario: Valore con barra verticale."""
    runner.call(
        RM, "register_session", "claude-1", "Claude", "4.8",
        "grep 'a|b' e poi\nvai a capo",
    )

    raw = Path(isolated_env["AGENT_REGISTRY_PATH"]).read_text()
    body = raw.split("---", 2)[2]
    rows = [l for l in body.splitlines() if l.strip().startswith("|")]

    assert len(rows) == 3, f"La tabella si e' rotta: {rows}"

    agent = runner.call(RM, "find_agent", "claude-1")
    assert agent["working_on"] == "grep 'a|b' e poi\nvai a capo", (
        "Il valore originale non e' stato preservato nel frontmatter"
    )


def test_pipe_is_escaped_inside_lists(runner, isolated_env):
    """Scenario: Valore con barra verticale (dentro una lista).

    `_fmt_list` della 0.1.0 non faceva escaping mentre `_fmt` sì: una lista
    contenente '|' apriva una colonna fantasma e disallineava la tabella.
    """
    runner.call(
        RM, "register_session", "claude-1", "Claude", "4.8", "x",
        space=["src/a|b.py", "src/normale.py"],
    )

    raw = Path(isolated_env["AGENT_REGISTRY_PATH"]).read_text()
    body = raw.split("---", 2)[2]
    rows = [l for l in body.splitlines() if l.strip().startswith("|")]

    assert len(rows) == 3, f"La tabella si e' rotta su una lista con '|': {rows}"
    header_cols = rows[0].count("|")
    agent_cols = rows[2].count("|")
    assert agent_cols == header_cols, (
        f"Colonne disallineate: header {header_cols}, riga agente {agent_cols}. "
        "Un '|' dentro una lista ha creato una colonna fantasma."
    )

    agent = runner.call(RM, "find_agent", "claude-1")
    assert agent["space"] == ["src/a|b.py", "src/normale.py"]


# --- Requirement: Riferimento all'handoff di sessione ------------------------


def test_add_handoff_ref(runner):
    """Scenario: Registrazione di un handoff."""
    runner.call(RM, "register_session", "claude-1", "Claude", "4.8", "x")

    runner.call(RM, "add_handoff_ref", "claude-1", ".handoff/HANDOFF-007.md")

    agent = runner.call(RM, "find_agent", "claude-1")
    assert agent["handoff"] == ".handoff/HANDOFF-007.md"


def test_find_agent_returns_none_when_absent(runner):
    assert runner.call(RM, "find_agent", "non-esiste") is None
