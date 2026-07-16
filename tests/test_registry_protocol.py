"""Test del protocollo auto-descrittivo nel registry.

Verifica: openspec/specs/agent-registry/spec.md — requisito
"Protocollo di coordinamento auto-descrittivo".

Il registry è l'unico artefatto che agenti di provider diversi toccano tutti:
le regole devono viaggiare con lo stato che descrivono, perché SKILL.md
istruisce solo chi la carica.
"""

from __future__ import annotations

from pathlib import Path

RM = "registry_manager"


def _read(isolated_env) -> str:
    return Path(isolated_env["AGENT_REGISTRY_PATH"]).read_text()


def test_protocol_present_in_new_registry(runner, isolated_env):
    """Scenario: Il protocollo è presente in un registry nuovo."""
    runner.call(RM, "ensure_registry")

    raw = _read(isolated_env)

    assert "PROTOCOL:START" in raw and "PROTOCOL:END" in raw
    assert "Do Not Touch" in raw, "Manca la regola sui file altrui"
    assert "advisory" in raw.lower(), (
        "Il blocco non dichiara che i lock sono advisory: un agente che crede "
        "in una garanzia inesistente e' il problema da cui nasce questo change."
    )


def test_protocol_survives_updates(runner, isolated_env):
    """Scenario: Il protocollo sopravvive agli aggiornamenti."""
    runner.call(RM, "register_session", "claude-1", "Claude", "4.8", "x")
    assert "PROTOCOL:START" in _read(isolated_env)

    runner.call(RM, "update_session", "claude-1", working_on="y")
    assert "PROTOCOL:START" in _read(isolated_env)

    runner.call(RM, "unregister_session", "claude-1")
    raw = _read(isolated_env)
    assert "PROTOCOL:START" in raw and "PROTOCOL:END" in raw
    assert "advisory" in raw.lower()


def test_protocol_does_not_break_parsing(runner, isolated_env):
    """Scenario: Il protocollo non interferisce col parse."""
    runner.call(RM, "register_session", "claude-1", "Claude", "4.8", "lavoro")

    agents = runner.call(RM, "load_agents")
    assert len(agents) == 1
    assert agents[0]["session_id"] == "claude-1"

    frontmatter, body = runner.call(RM, "parse_registry")
    assert frontmatter["version"] == "1.0"
    assert "PROTOCOL:START" in body


def test_tampered_protocol_is_restored(runner, isolated_env):
    """Scenario: Un blocco manomesso viene ripristinato."""
    runner.call(RM, "register_session", "claude-1", "Claude", "4.8", "x")

    registry = Path(isolated_env["AGENT_REGISTRY_PATH"])
    raw = registry.read_text()
    frontmatter_end = raw.index("---", 3) + 3
    registry.write_text(raw[:frontmatter_end] + "\n\nHo cancellato le regole.\n")
    assert "PROTOCOL:START" not in registry.read_text()

    runner.call(RM, "update_session", "claude-1", working_on="y")

    restored = registry.read_text()
    assert "PROTOCOL:START" in restored, (
        "Il blocco non e' stato ripristinato: un registry senza regole fa "
        "concludere a un agente che non ce ne siano."
    )
    assert "advisory" in restored.lower()
    assert "Ho cancellato le regole." not in restored


def test_protocol_appears_once(runner, isolated_env):
    """Il blocco non deve accumularsi a ogni scrittura."""
    runner.call(RM, "register_session", "claude-1", "Claude", "4.8", "x")
    for i in range(3):
        runner.call(RM, "update_session", "claude-1", working_on=f"giro {i}")

    raw = _read(isolated_env)
    assert raw.count("PROTOCOL:START") == 1, "Il blocco si e' duplicato"


def test_template_matches_canonical_block(runner):
    """Il template su disco non deve divergere dal blocco generato.

    Sono due copie della stessa verità: se il template resta indietro, chi
    legge il repo trova regole diverse da quelle che il codice scrive.
    """
    template = Path(__file__).parent.parent / "templates" / "registry-template.md"
    assert template.exists(), "templates/registry-template.md mancante"

    canonical = runner.call(RM, "get_protocol_block")
    assert canonical in template.read_text(), (
        "Il template diverge dal blocco canonico generato da registry_manager."
    )
