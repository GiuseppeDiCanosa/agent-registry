#!/usr/bin/env python3
"""Manager per il registry condiviso agent-registry.

Il registry è un file markdown con YAML frontmatter + tabella markdown.
Path di default: ~/Desktop/agent-registry/registry.md
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

DEFAULT_REGISTRY_PATH = Path.home() / "Desktop" / "agent-registry" / "registry.md"
TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "registry-template.md"


def get_registry_path() -> Path:
    """Restituisce il path del registry, sovrascrivibile via env AGENT_REGISTRY_PATH."""
    env = os.environ.get("AGENT_REGISTRY_PATH")
    return Path(env) if env else DEFAULT_REGISTRY_PATH


def ensure_registry(registry_path: Path | None = None) -> Path:
    """Crea il file registry se non esiste, usando il template."""
    path = registry_path or get_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        if TEMPLATE_PATH.exists():
            shutil.copy(TEMPLATE_PATH, path)
        else:
            path.write_text(_empty_registry(), encoding="utf-8")
    return path


def _empty_registry() -> str:
    return """---\nversion: "1.0"\nlast_updated: ""\nagents: []\n---\n\n| Session ID | Provider | AI Version | Started At (Rome) | Working On | To Do Past | To Do Present | To Do Future | Space | Do Not Touch | Status | Issues | Handoff |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"""


def parse_registry(registry_path: Path | None = None) -> tuple[dict[str, Any], str]:
    """Parsa il registry restituendo (frontmatter, body markdown)."""
    path = registry_path or get_registry_path()
    ensure_registry(path)
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError(f"Formato registry non valido: {path}")
    frontmatter = yaml.safe_load(match.group(1)) or {}
    body = match.group(2)
    return frontmatter, body


def load_agents(registry_path: Path | None = None) -> list[dict[str, Any]]:
    """Carica la lista agenti dal registry."""
    frontmatter, _ = parse_registry(registry_path)
    return frontmatter.get("agents", []) or []


def _now_rome() -> str:
    """Timestamp corrente in timezone Roma."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        # Fallback: usa l'orario locale
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def _iso_now() -> str:
    """Timestamp ISO 8601 con timezone per last_updated."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Rome")).isoformat()
    except Exception:
        return datetime.now().astimezone().isoformat()


def _render_table(agents: list[dict[str, Any]]) -> str:
    """Genera la tabella markdown dagli agenti."""
    header = (
        "| Session ID | Provider | AI Version | Started At (Rome) | "
        "Working On | To Do Past | To Do Present | To Do Future | "
        "Space | Do Not Touch | Status | Issues | Handoff |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for a in agents:
        todo = a.get("todo") or {}
        lines.append(
            f"| {_fmt(a.get('session_id'))} | {_fmt(a.get('provider'))} | "
            f"{_fmt(a.get('ai_version'))} | {_fmt(a.get('started_at'))} | "
            f"{_fmt(a.get('working_on'))} | {_fmt_list(todo.get('past', []))} | "
            f"{_fmt_list(todo.get('present', []))} | {_fmt_list(todo.get('future', []))} | "
            f"{_fmt_list(a.get('space', []))} | {_fmt_list(a.get('do_not_touch', []))} | "
            f"{_fmt(a.get('status'))} | {_fmt(a.get('issues'))} | {_fmt(a.get('handoff'))} |"
        )
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _fmt_list(items: list[Any] | None) -> str:
    if not items:
        return ""
    return ", ".join(str(x) for x in items)


def _dump_registry(agents: list[dict[str, Any]]) -> str:
    """Serializza il registry completo."""
    frontmatter = {
        "version": "1.0",
        "last_updated": _iso_now(),
        "agents": agents,
    }
    table = _render_table(agents)
    yaml_part = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return f"---\n{yaml_part}---\n\n{table}\n"


def save_agents(
    agents: list[dict[str, Any]], registry_path: Path | None = None
) -> Path:
    """Scrive atomicamente la lista agenti nel registry, con lock."""
    path = registry_path or get_registry_path()
    ensure_registry(path)
    content = _dump_registry(agents)

    # Lock esclusivo sul registry per serializzare le scritture concorrenti
    with open(path, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            tmp_fd, tmp_name = tempfile.mkstemp(
                dir=path.parent, prefix=".registry_", suffix=".tmp", text=True
            )
            try:
                os.write(tmp_fd, content.encode("utf-8"))
            finally:
                os.close(tmp_fd)
            shutil.move(tmp_name, path)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return path


def register_session(
    session_id: str,
    provider: str,
    ai_version: str,
    working_on: str,
    space: list[str] | None = None,
    todo_present: list[str] | None = None,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """Registra una nuova sessione agente nel registry."""
    agents = load_agents(registry_path)
    # Rimuovi eventuale sessione precedente con lo stesso ID
    agents = [a for a in agents if a.get("session_id") != session_id]
    agent = {
        "session_id": session_id,
        "provider": provider,
        "ai_version": ai_version,
        "started_at": _now_rome(),
        "working_on": working_on,
        "todo": {
            "past": [],
            "present": todo_present or [],
            "future": [],
        },
        "space": space or [],
        "do_not_touch": [],
        "status": "OnWorking",
        "issues": "",
        "handoff": "",
    }
    agents.append(agent)
    save_agents(agents, registry_path)
    return agent


def update_session(
    session_id: str,
    registry_path: Path | None = None,
    **kwargs: Any,
) -> dict[str, Any] | None:
    """Aggiorna i campi di una sessione esistente."""
    agents = load_agents(registry_path)
    for agent in agents:
        if agent.get("session_id") == session_id:
            for key, value in kwargs.items():
                if key == "todo" and isinstance(value, dict):
                    agent.setdefault("todo", {}).update(value)
                else:
                    agent[key] = value
            save_agents(agents, registry_path)
            return agent
    return None


def unregister_session(
    session_id: str, registry_path: Path | None = None
) -> dict[str, Any] | None:
    """Marca una sessione come Finished e rimuove i lock."""
    agents = load_agents(registry_path)
    for agent in agents:
        if agent.get("session_id") == session_id:
            agent["status"] = "Finished"
            agent["do_not_touch"] = []
            save_agents(agents, registry_path)
            return agent
    return None


def find_agent(
    session_id: str, registry_path: Path | None = None
) -> dict[str, Any] | None:
    """Trova un agente per session_id."""
    for agent in load_agents(registry_path):
        if agent.get("session_id") == session_id:
            return agent
    return None


def add_handoff_ref(
    session_id: str, handoff_path: str, registry_path: Path | None = None
) -> dict[str, Any] | None:
    """Aggiunge il riferimento a un handoff salvato."""
    return update_session(
        session_id, registry_path=registry_path, handoff=handoff_path
    )


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "register":
        agent = register_session(
            session_id=sys.argv[2],
            provider=sys.argv[3],
            ai_version=sys.argv[4],
            working_on=sys.argv[5],
            space=sys.argv[6].split(",") if len(sys.argv) > 6 else [],
            todo_present=sys.argv[7].split(",") if len(sys.argv) > 7 else [],
        )
        print(f"Registrato: {agent['session_id']}")
    elif cmd == "update":
        update_session(sys.argv[2], working_on=sys.argv[3] if len(sys.argv) > 3 else "")
        print("Aggiornato.")
    elif cmd == "finish":
        unregister_session(sys.argv[2])
        print("Sessione terminata.")
    elif cmd == "show":
        for a in load_agents():
            print(a)
    elif cmd == "handoff":
        session_id, handoff_path = sys.argv[2], sys.argv[3]
        add_handoff_ref(session_id, handoff_path)
        print("Handoff registrato.")
    else:
        print(
            "Uso: registry_manager.py register <session_id> <provider> <ai_version> <working_on> [space] [todo_present]\n"
            "     registry_manager.py update <session_id> [working_on]\n"
            "     registry_manager.py finish <session_id>\n"
            "     registry_manager.py handoff <session_id> <handoff_path>\n"
            "     registry_manager.py show"
        )
