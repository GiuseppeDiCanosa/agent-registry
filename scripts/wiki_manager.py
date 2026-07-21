#!/usr/bin/env python3
"""Manager per la wiki delle sessioni agent-registry.

La wiki è la memoria persistente delle sessioni: ogni sessione chiusa viene
distillata in un entry markdown `wiki/<session_id>.md` (fonte di verità,
sincronizzata via git) indicizzato in un DB SQLite locale `wiki.db`
(non sincronizzato, ricostruibile con `wiki rebuild`).

Schema DB:
  - tabella `wiki_entries`: una riga per entry, con i campi del frontmatter
    (le liste serializzate in JSON), più `status` ('ok' | 'pending_ingest')
    e `context_md` (path relativo del context file);
  - tabella FTS5 `wiki_fts` su (router, cosa_fatto, bug_trovati),
    sincronizzata via trigger; se FTS5 non è disponibile nella build di
    sqlite3, `search()` ricade su una query LIKE (più lenta, stesso output).

Dipendenze: solo stdlib + pyyaml. La home del registry è risolta via
registry_manager.get_registry_home() (env AGENT_REGISTRY_HOME).
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import yaml

# Campi lista del frontmatter (serializzati JSON nel DB, liste YAML nel markdown).
LIST_FIELDS = ("file_toccati", "git_push", "handoff", "bug_trovati", "skill_tool_mcp")
# Campi scalari del frontmatter (in ordine canonico, usato per la scrittura).
SCALAR_FIELDS = (
    "id",
    "session_id",
    "provider",
    "modello",
    "data",
    "router",
    "cosa_fatto",
    "come_fatto",
    "problema_risolto",
)
ALL_FIELDS = SCALAR_FIELDS + LIST_FIELDS
# Colonne indicizzate full-text (D7).
FTS_FIELDS = ("router", "cosa_fatto", "bug_trovati")

STATUS_OK = "ok"
STATUS_PENDING = "pending_ingest"

# Cache della disponibilità FTS5 (None = non ancora verificato).
_FTS5_AVAILABLE: bool | None = None


def _registry_home() -> Path:
    """Home del registry via registry_manager (lazy import, anti circolari)."""
    scripts_dir = str(Path(__file__).parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from registry_manager import get_registry_home

    return get_registry_home()


def _db_path(home: Path | None = None) -> Path:
    """Path del DB wiki nella home."""
    return (home or _registry_home()) / "wiki.db"


def _wiki_dir(home: Path | None = None) -> Path:
    return (home or _registry_home()) / "wiki"


def _fts5_available() -> bool:
    """True se la build di sqlite3 supporta FTS5 (verificato una sola volta)."""
    global _FTS5_AVAILABLE
    if _FTS5_AVAILABLE is None:
        try:
            con = sqlite3.connect(":memory:")
            con.execute("CREATE VIRTUAL TABLE t USING fts5(a)")
            con.close()
            _FTS5_AVAILABLE = True
        except sqlite3.OperationalError:
            _FTS5_AVAILABLE = False
    return _FTS5_AVAILABLE


def _connect(home: Path | None = None) -> sqlite3.Connection:
    """Apre il DB creando lo schema se mancante."""
    home = home or _registry_home()
    home.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_db_path(home)))
    con.row_factory = sqlite3.Row
    _create_schema(con)
    return con


def _create_schema(con: sqlite3.Connection) -> None:
    """Crea tabella wiki_entries e indice FTS5 (con trigger) se mancanti."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS wiki_entries (
            id INTEGER PRIMARY KEY,
            session_id TEXT UNIQUE NOT NULL,
            provider TEXT DEFAULT '',
            modello TEXT DEFAULT '',
            data TEXT DEFAULT '',
            router TEXT DEFAULT '',
            cosa_fatto TEXT DEFAULT '',
            come_fatto TEXT DEFAULT '',
            problema_risolto TEXT DEFAULT '',
            file_toccati TEXT DEFAULT '[]',
            git_push TEXT DEFAULT '[]',
            handoff TEXT DEFAULT '[]',
            bug_trovati TEXT DEFAULT '[]',
            skill_tool_mcp TEXT DEFAULT '[]',
            status TEXT DEFAULT 'ok',
            context_md TEXT DEFAULT ''
        )
        """
    )
    if _fts5_available():
        con.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5("
            "router, cosa_fatto, bug_trovati, "
            "content='wiki_entries', content_rowid='id')"
        )
        # Trigger di sincronizzazione (external content): l'indice segue
        # automaticamente insert/update/delete su wiki_entries.
        con.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS wiki_entries_ai AFTER INSERT ON wiki_entries BEGIN
                INSERT INTO wiki_fts(rowid, router, cosa_fatto, bug_trovati)
                VALUES (new.id, new.router, new.cosa_fatto, new.bug_trovati);
            END;
            CREATE TRIGGER IF NOT EXISTS wiki_entries_ad AFTER DELETE ON wiki_entries BEGIN
                INSERT INTO wiki_fts(wiki_fts, rowid, router, cosa_fatto, bug_trovati)
                VALUES ('delete', old.id, old.router, old.cosa_fatto, old.bug_trovati);
            END;
            CREATE TRIGGER IF NOT EXISTS wiki_entries_au AFTER UPDATE ON wiki_entries BEGIN
                INSERT INTO wiki_fts(wiki_fts, rowid, router, cosa_fatto, bug_trovati)
                VALUES ('delete', old.id, old.router, old.cosa_fatto, old.bug_trovati);
                INSERT INTO wiki_fts(rowid, router, cosa_fatto, bug_trovati)
                VALUES (new.id, new.router, new.cosa_fatto, new.bug_trovati);
            END;
            """
        )
    con.commit()


# --- Frontmatter markdown (fonte di verità) ---


def _to_list(value: Any) -> list[str]:
    """Normalizza un campo lista: accetta lista, CSV o stringa singola."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [text]


def normalize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Normalizza un dict di campi entry: liste come list[str], scalari come str."""
    out: dict[str, Any] = {}
    for key in SCALAR_FIELDS:
        if key == "id":
            continue
        value = fields.get(key)
        out[key] = "" if value is None else str(value)
    for key in LIST_FIELDS:
        out[key] = _to_list(fields.get(key))
    return out


def render_entry_markdown(fields: dict[str, Any], body: str) -> str:
    """Renderizza un wiki entry markdown (frontmatter YAML + body)."""
    frontmatter: dict[str, Any] = {}
    for key in ALL_FIELDS:
        if key == "id":
            frontmatter[key] = int(fields.get("id") or 0)
        elif key in LIST_FIELDS:
            frontmatter[key] = _to_list(fields.get(key))
        else:
            value = fields.get(key)
            frontmatter[key] = "" if value is None else str(value)
    yaml_part = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    body = (body or "").strip()
    return f"---\n{yaml_part}---\n\n{body}\n"


def parse_entry_markdown(text: str) -> tuple[dict[str, Any], str]:
    """Parsa un wiki entry markdown restituendo (campi frontmatter, body)."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError("Wiki entry senza frontmatter YAML valido")
    fields = yaml.safe_load(match.group(1)) or {}
    if not isinstance(fields, dict):
        raise ValueError("Frontmatter wiki entry non è un mapping")
    return fields, match.group(2)


def write_wiki_entry(
    session_id: str,
    fields: dict[str, Any],
    body: str,
    home: Path | None = None,
) -> Path:
    """Scrive atomicamente `wiki/<session_id>.md` (fonte di verità)."""
    scripts_dir = str(Path(__file__).parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from registry_manager import _atomic_write, _session_filename

    home = home or _registry_home()
    wiki_dir = _wiki_dir(home)
    wiki_dir.mkdir(parents=True, exist_ok=True)
    filename = _session_filename(session_id).removesuffix(".yaml") + ".md"
    path = wiki_dir / filename
    _atomic_write(path, render_entry_markdown(fields, body))
    return path


# --- DB: upsert, rebuild, search ---


def _next_id(con: sqlite3.Connection) -> int:
    """Id progressivo: max(id)+1."""
    row = con.execute("SELECT MAX(id) AS m FROM wiki_entries").fetchone()
    return int(row["m"] or 0) + 1


def entry_id_for(session_id: str, home: Path | None = None) -> int:
    """Id DB per una sessione: quello esistente se già presente, altrimenti il prossimo."""
    con = _connect(home)
    try:
        row = con.execute(
            "SELECT id FROM wiki_entries WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row["id"]) if row else _next_id(con)
    finally:
        con.close()


def upsert_entry(
    session_id: str,
    fields: dict[str, Any],
    home: Path | None = None,
) -> int:
    """Inserisce o aggiorna la riga DB per una sessione; restituisce l'id.

    Se la riga esiste già (stesso session_id) l'id esistente è conservato,
    salvo che `fields` non specifichi un id diverso. Lo status è
    'pending_ingest' se il campo router è vuoto (lo riempirà l'ingestione),
    altrimenti 'ok' (salvo status esplicito in fields).
    """
    norm = normalize_fields({**fields, "session_id": session_id})
    context_md = str(fields.get("context_md") or "")
    status = fields.get("status")
    if not status:
        status = STATUS_PENDING if not norm["router"].strip() else STATUS_OK

    con = _connect(home)
    try:
        existing = con.execute(
            "SELECT id FROM wiki_entries WHERE session_id = ?", (session_id,)
        ).fetchone()
        requested_id = fields.get("id")
        if existing is not None:
            entry_id = int(existing["id"])
        elif requested_id:
            entry_id = int(requested_id)
        else:
            entry_id = _next_id(con)

        row = {
            "id": entry_id,
            "session_id": session_id,
            "status": status,
            "context_md": context_md,
        }
        for key in SCALAR_FIELDS:
            if key in ("id", "session_id"):
                continue
            row[key] = norm[key]
        for key in LIST_FIELDS:
            row[key] = json.dumps(norm[key], ensure_ascii=False)

        con.execute(
            """
            INSERT INTO wiki_entries (
                id, session_id, provider, modello, data, router,
                cosa_fatto, come_fatto, problema_risolto,
                file_toccati, git_push, handoff, bug_trovati, skill_tool_mcp,
                status, context_md
            ) VALUES (
                :id, :session_id, :provider, :modello, :data, :router,
                :cosa_fatto, :come_fatto, :problema_risolto,
                :file_toccati, :git_push, :handoff, :bug_trovati, :skill_tool_mcp,
                :status, :context_md
            )
            ON CONFLICT(session_id) DO UPDATE SET
                provider=excluded.provider, modello=excluded.modello,
                data=excluded.data, router=excluded.router,
                cosa_fatto=excluded.cosa_fatto, come_fatto=excluded.come_fatto,
                problema_risolto=excluded.problema_risolto,
                file_toccati=excluded.file_toccati, git_push=excluded.git_push,
                handoff=excluded.handoff, bug_trovati=excluded.bug_trovati,
                skill_tool_mcp=excluded.skill_tool_mcp,
                status=excluded.status, context_md=excluded.context_md
            """,
            row,
        )
        con.commit()
        return entry_id
    finally:
        con.close()


def rebuild(home: Path | None = None) -> int:
    """Cancella e ricostruisce l'intero DB dai file `wiki/*.md` (fonte di verità).

    Restituisce il numero di entry indicizzati. Gli entry con `router` vuoto
    finiscono in status 'pending_ingest' (li riprenderà l'ingestione).
    I file non parsabili vengono saltati con un avviso su stderr.
    """
    home = home or _registry_home()
    db = _db_path(home)
    if db.exists():
        db.unlink()

    count = 0
    for md_file in sorted(_wiki_dir(home).glob("*.md")):
        try:
            fields, _body = parse_entry_markdown(md_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[wiki_manager] rebuild: skip {md_file.name}: {e}", file=sys.stderr)
            continue
        session_id = str(fields.get("session_id") or md_file.stem)
        # context_md non è nel frontmatter: deriva dal context file se esiste.
        context_rel = f"contexts/{session_id}-context.md"
        if not (home / context_rel).exists():
            context_rel = ""
        upsert_entry(
            session_id,
            {**fields, "context_md": context_rel},
            home=home,
        )
        count += 1
    return count


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Converte una riga DB in dict con le liste deserializzate da JSON."""
    out = dict(row)
    for key in LIST_FIELDS:
        try:
            out[key] = json.loads(out.get(key) or "[]")
        except (json.JSONDecodeError, TypeError):
            out[key] = []
    return out


def list_entries(
    limit: int = 50,
    offset: int = 0,
    home: Path | None = None,
) -> list[dict[str, Any]]:
    """Ultime entry del DB wiki (id desc), con liste deserializzate."""
    con = _connect(home)
    try:
        rows = con.execute(
            "SELECT * FROM wiki_entries ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        con.close()


def get_entry(entry_id: int, home: Path | None = None) -> dict[str, Any] | None:
    """Dettaglio completo di una entry per id; None se non esiste."""
    con = _connect(home)
    try:
        row = con.execute(
            "SELECT * FROM wiki_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        con.close()


def _fts_query(query_text: str) -> str:
    """Costruisce una query FTS5 sicura: token quotati uniti in OR."""
    tokens = re.findall(r"\w+", query_text, re.UNICODE)
    return " OR ".join(f'"{t}"' for t in tokens)


def search(
    query_text: str,
    home: Path | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Ricerca full-text su router/cosa_fatto/bug_trovati con ranking.

    Usa FTS5 (bm25) se disponibile, altrimenti ricade su LIKE. Restituisce
    una lista di dict (tutte le colonne, liste deserializzate), al massimo
    `limit` risultati ordinati per pertinenza.
    """
    tokens = re.findall(r"\w+", query_text, re.UNICODE)
    if not tokens:
        return []
    con = _connect(home)
    try:
        if _fts5_available():
            try:
                rows = con.execute(
                    """
                    SELECT e.*, bm25(wiki_fts) AS rank
                    FROM wiki_fts
                    JOIN wiki_entries e ON e.id = wiki_fts.rowid
                    WHERE wiki_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (_fts_query(query_text), limit),
                ).fetchall()
                return [_row_to_dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass  # fallback LIKE
        clauses: list[str] = []
        params: list[str] = []
        for token in tokens:
            clauses.append(
                "(" + " OR ".join(f"{field} LIKE ?" for field in FTS_FIELDS) + ")"
            )
            params.extend([f"%{token}%"] * len(FTS_FIELDS))
        rows = con.execute(
            f"SELECT * FROM wiki_entries WHERE {' OR '.join(clauses)} LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        con.close()
