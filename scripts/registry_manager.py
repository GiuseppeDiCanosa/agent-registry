#!/usr/bin/env python3
# GENERATED FROM SPEC — DO NOT EDIT DIRECTLY
# Source: openspec/specs/agent-registry/spec.md
"""Registry condiviso degli agenti AI CLI attivi su un progetto.

Il registry è un markdown con frontmatter YAML (il dato autorevole) e una
tabella markdown (la vista leggibile), rigenerata a ogni scrittura.

Concorrenza: l'intero ciclo read-modify-write avviene dentro un flock tenuto
su `registry.lock`, un file **dedicato e mai rinominato**. La 0.1.0 lockava
`registry.md` e poi lo sostituiva con `shutil.move`: dopo il rename il path
puntava a un inode nuovo, quindi il writer successivo lockava un oggetto
diverso e i due non si escludevano affatto. Lock su un file, scrittura
atomica su un altro: le due cose non devono mai coincidere.
"""

from __future__ import annotations

import fcntl
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

DEFAULT_REGISTRY_PATH = Path.home() / "Desktop" / "agent-registry" / "registry.md"

# Istruzioni per qualunque agente apra il registry. Gli agenti dei vari
# provider non condividono alcun sistema di skill: SKILL.md istruisce solo chi
# la carica. Il registry invece lo aprono tutti, quindi le regole viaggiano
# con lo stato che descrivono. Rigenerato a ogni scrittura: un blocco che si
# può perdere con un update è un blocco su cui non si può contare.
PROTOCOL_BLOCK = """<!-- PROTOCOL:START — rigenerato a ogni scrittura, non modificare a mano -->
# AGENT REGISTRY — PROTOCOLLO OBBLIGATORIO

> **Se stai leggendo questo file come agente AI, queste regole valgono per te.**
>
> 1. **Leggi prima di toccare.** Non modificare alcun file elencato nella
>    colonna `Do Not Touch` di un agente con status `OnWorking`: c'è un altro
>    agente che ci sta lavorando in questo momento.
> 2. **Registrati prima di lavorare:**
>    `python <skill>/scripts/registry_manager.py register <session_id> <provider> <versione> "<cosa stai facendo>" "<file,toccati>" "<todo,correnti>"`
> 3. **Acquisisci il lock prima di ogni modifica:**
>    `python <skill>/scripts/lock_manager.py acquire <path> <session_id>`
>    Exit code 0 = il lock è tuo; diverso da 0 = è di un altro, **fermati**.
> 4. **Tieni vivo il lock** se il lavoro supera i 120s: `heartbeat-loop`.
> 5. **A fine sessione:** `registry_manager.py finish <session_id>`, che
>    rilascia anche i lock della sessione.
>
> **I lock sono advisory.** Nessuno impedisce fisicamente la scrittura su un
> file bloccato: la protezione funziona solo se ogni agente rispetta questo
> protocollo. Se lo ignori, il coordinamento salta per tutti.
<!-- PROTOCOL:END -->"""

_TABLE_HEADER = (
    "| Session ID | Provider | AI Version | Started At (Rome) | "
    "Working On | To Do Past | To Do Present | To Do Future | "
    "Space | Do Not Touch | Status | Issues | Handoff |"
)
_TABLE_SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|---|"


def get_protocol_block() -> str:
    """Blocco di protocollo canonico.

    Esposto come funzione perché è la sola definizione autorevole: chi ne
    vuole una copia (il template su disco, un test) deve leggerla da qui,
    non riscriverla.
    """
    return PROTOCOL_BLOCK


def get_registry_path() -> Path:
    """Path del registry, sovrascrivibile via AGENT_REGISTRY_PATH.

    Risolto a ogni chiamata, mai all'import: il default dipende da HOME e
    congelarlo renderebbe il modulo isolabile solo in-process.
    """
    env = os.environ.get("AGENT_REGISTRY_PATH")
    return Path(env) if env else DEFAULT_REGISTRY_PATH


def _lock_path(registry_path: Path) -> Path:
    """File di lock dedicato, accanto al registry.

    Non è mai rinominato né cancellato: è ciò che permette a processi diversi
    di flockare lo stesso inode mentre `registry.md` viene sostituito.
    """
    return registry_path.with_name(registry_path.name + ".lock")


@contextmanager
def _registry_critical(registry_path: Path):
    """Serializza l'intero read-modify-write del registry."""
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_lock_path(registry_path)), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _now_rome() -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def _iso_now() -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Rome")).isoformat()
    except Exception:
        return datetime.now().astimezone().isoformat()


def _fmt(value: Any) -> str:
    """Neutralizza i caratteri che romperebbero una cella di tabella."""
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _fmt_list(items: list[Any] | None) -> str:
    """Come _fmt, ma per le liste.

    La 0.1.0 faceva l'escape solo negli scalari: un '|' dentro `space` apriva
    una colonna fantasma e disallineava la tabella.
    """
    if not items:
        return ""
    return ", ".join(_fmt(x) for x in items)


def _render_table(agents: list[dict[str, Any]]) -> str:
    lines = [_TABLE_HEADER, _TABLE_SEP]
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


def _dump_registry(agents: list[dict[str, Any]]) -> str:
    frontmatter = {"version": "1.0", "last_updated": _iso_now(), "agents": agents}
    yaml_part = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return f"---\n{yaml_part}---\n\n{PROTOCOL_BLOCK}\n\n{_render_table(agents)}\n"


def _write_atomic(path: Path, content: str) -> None:
    """Scrive via file temporaneo + replace.

    Sostituire l'inode qui è sicuro solo perché il flock è tenuto su
    `registry.lock`, un file diverso: era la coincidenza fra i due a rendere
    inutile il lock nella 0.1.0.
    """
    tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".registry_", suffix=".tmp")
    try:
        os.write(tmp_fd, content.encode("utf-8"))
        os.fsync(tmp_fd)
    finally:
        os.close(tmp_fd)
    os.replace(tmp_name, path)


def _parse_text(text: str, source: Path) -> tuple[dict[str, Any], str]:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError(f"Formato registry non valido: {source}")
    frontmatter = yaml.safe_load(match.group(1)) or {}
    return frontmatter, match.group(2)


def _load_unlocked(path: Path) -> list[dict[str, Any]]:
    """Legge gli agenti stando già dentro la sezione critica."""
    if not path.exists():
        return []
    frontmatter, _ = _parse_text(path.read_text(encoding="utf-8"), path)
    return frontmatter.get("agents", []) or []


def ensure_registry(registry_path: Path | None = None) -> Path:
    """Crea il registry se non esiste."""
    path = registry_path or get_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with _registry_critical(path):
            if not path.exists():  # ricontrollo dentro la sezione critica
                _write_atomic(path, _dump_registry([]))
    return path


def parse_registry(registry_path: Path | None = None) -> tuple[dict[str, Any], str]:
    """Parsa il registry restituendo (frontmatter, body)."""
    path = registry_path or get_registry_path()
    ensure_registry(path)
    return _parse_text(path.read_text(encoding="utf-8"), path)


def load_agents(registry_path: Path | None = None) -> list[dict[str, Any]]:
    """Lista degli agenti. Il frontmatter è l'unica fonte autorevole."""
    frontmatter, _ = parse_registry(registry_path)
    return frontmatter.get("agents", []) or []


def save_agents(agents: list[dict[str, Any]], registry_path: Path | None = None) -> Path:
    """Scrive la lista agenti, sostituendo l'intero contenuto."""
    path = registry_path or get_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _registry_critical(path):
        _write_atomic(path, _dump_registry(agents))
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
    """Registra una sessione. Un id già presente viene sostituito."""
    path = registry_path or get_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    agent = {
        "session_id": session_id,
        "provider": provider,
        "ai_version": ai_version,
        "started_at": _now_rome(),
        "working_on": working_on,
        "todo": {"past": [], "present": todo_present or [], "future": []},
        "space": space or [],
        "do_not_touch": [],
        "status": "OnWorking",
        "issues": "",
        "handoff": "",
    }
    # Lettura e scrittura nella stessa sezione critica: leggere fuori dal lock
    # è ciò che faceva sopravvivere 1 registrazione su 8 nella 0.1.0.
    with _registry_critical(path):
        agents = [a for a in _load_unlocked(path) if a.get("session_id") != session_id]
        agents.append(agent)
        _write_atomic(path, _dump_registry(agents))
    return agent


def update_session(
    session_id: str, registry_path: Path | None = None, **kwargs: Any
) -> dict[str, Any] | None:
    """Aggiorna i campi di una sessione. None se la sessione non esiste."""
    path = registry_path or get_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _registry_critical(path):
        agents = _load_unlocked(path)
        for agent in agents:
            if agent.get("session_id") == session_id:
                for key, value in kwargs.items():
                    if key == "todo" and isinstance(value, dict):
                        agent.setdefault("todo", {}).update(value)
                    else:
                        agent[key] = value
                _write_atomic(path, _dump_registry(agents))
                return agent
    return None


def unregister_session(
    session_id: str, registry_path: Path | None = None
) -> dict[str, Any] | None:
    """Chiude una sessione: Finished, do_not_touch svuotato, lock rilasciati.

    Rilasciare i lock qui elimina la divergenza fra registry e `locks/`: la
    0.1.0 svuotava `do_not_touch` ma lasciava i lock appesi fino al timeout,
    chiedendo all'agente di rilasciarli a mano.
    """
    path = registry_path or get_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with _registry_critical(path):
        agents = _load_unlocked(path)
        target = next((a for a in agents if a.get("session_id") == session_id), None)
        if target is None:
            return None
        held = list(target.get("do_not_touch") or [])
        target["status"] = "Finished"
        target["do_not_touch"] = []
        _write_atomic(path, _dump_registry(agents))

    # Fuori dalla sezione critica del registry: i lock hanno il proprio.
    # release_lock rifiuta i path di cui la sessione non è owner.
    if held:
        import lock_manager

        for locked_path in held:
            try:
                lock_manager.release_lock(locked_path, session_id)
            except Exception as e:  # un lock illeggibile non deve bloccare la chiusura
                print(
                    f"Attenzione: rilascio di '{locked_path}' fallito: {e}",
                    file=sys.stderr,
                )
    return target


def find_agent(session_id: str, registry_path: Path | None = None) -> dict[str, Any] | None:
    for agent in load_agents(registry_path):
        if agent.get("session_id") == session_id:
            return agent
    return None


def add_handoff_ref(
    session_id: str, handoff_path: str, registry_path: Path | None = None
) -> dict[str, Any] | None:
    return update_session(session_id, registry_path=registry_path, handoff=handoff_path)


USAGE = """Uso: registry_manager.py register <session_id> <provider> <ai_version> <working_on> [space] [todo_present]
     registry_manager.py update <session_id> [working_on]
     registry_manager.py finish <session_id>
     registry_manager.py handoff <session_id> <handoff_path>
     registry_manager.py show

Exit code: 0 se l'operazione riesce, 1 se fallisce."""


def main(argv: list[str]) -> int:
    """CLI. Un'operazione non avvenuta non deve mai uscire con 0."""
    cmd = argv[1] if len(argv) > 1 else "help"
    try:
        if cmd == "register":
            agent = register_session(
                session_id=argv[2],
                provider=argv[3],
                ai_version=argv[4],
                working_on=argv[5],
                space=argv[6].split(",") if len(argv) > 6 else [],
                todo_present=argv[7].split(",") if len(argv) > 7 else [],
            )
            print(f"Registrato: {agent['session_id']}")
            return 0
        if cmd == "update":
            updated = update_session(argv[2], working_on=argv[3] if len(argv) > 3 else "")
            if updated is None:
                print(f"Errore: sessione '{argv[2]}' non trovata.", file=sys.stderr)
                return 1
            print("Aggiornato.")
            return 0
        if cmd == "finish":
            if unregister_session(argv[2]) is None:
                print(f"Errore: sessione '{argv[2]}' non trovata.", file=sys.stderr)
                return 1
            print("Sessione terminata.")
            return 0
        if cmd == "handoff":
            if add_handoff_ref(argv[2], argv[3]) is None:
                print(f"Errore: sessione '{argv[2]}' non trovata.", file=sys.stderr)
                return 1
            print("Handoff registrato.")
            return 0
        if cmd == "show":
            for a in load_agents():
                print(a)
            return 0
    except IndexError:
        print(f"Errore: argomenti mancanti per '{cmd}'.\n\n{USAGE}", file=sys.stderr)
        return 1

    print(USAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
