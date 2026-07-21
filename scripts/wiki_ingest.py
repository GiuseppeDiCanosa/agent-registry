#!/usr/bin/env python3
"""Ingestione wiki e router query via LangChain + Kimi (decisione D8).

Due responsabilità:

1. **Ingestione** (`ingest_entry`, `ingest_pending`): legge
   `wiki/<session_id>.md` (fonte di verità), chiede all'LLM Kimi di generare
   il campo `router` (descrizione breve scritta per essere riconosciuta da
   un'AI come pertinente a una richiesta futura) e di migliorare i campi
   narrativi rimasti "non documentato"; aggiorna markdown e DB, status -> 'ok'.

2. **Router query** (`router_query`): pre-filtro FTS5 via wiki_manager.search,
   poi ranking LLM sui candidati che restituisce id + motivazione. Se non ci
   sono candidati FTS la risposta è "non risulta svolto in passato" SENZA
   chiamare l'LLM.

Fallback (D8): se l'LLM non è raggiungibile, la API key manca o l'output non
è JSON valido dopo i retry, l'entry resta `pending_ingest`, markdown e DB
restano intatti e nessuna eccezione fatale viene sollevata. Nelle query il
fallback restituisce i candidati FTS senza ranking.

Configurazione via env:
  - KIMI_API_KEY (o fallback MOONSHOT_API_KEY): API key del provider;
  - KIMI_BASE_URL: endpoint OpenAI-compatibile (default https://api.moonshot.ai/v1);
  - KIMI_MODEL: modello (default kimi-k2.5).

L'LLM è iniettabile: tutte le funzioni accettano `complete`, una callable
`prompt -> str`, così i test non fanno mai chiamate di rete.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

# Callable di completamento: riceve il prompt e restituisce il testo dell'LLM.
CompleteFn = Callable[[str], str]

DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"
DEFAULT_MODEL = "kimi-k2.5"
# Retry sul parsing JSON: 1 tentativo iniziale + MAX_JSON_RETRIES retry.
MAX_JSON_RETRIES = 2
ROUTER_MAX_CHARS = 300
MAX_QUERY_CANDIDATES = 10
NON_DOCUMENTATO = "non documentato"
# Campi narrativi che l'LLM può migliorare solo se "non documentato".
IMPROVABLE_FIELDS = ("cosa_fatto", "come_fatto", "problema_risolto")


class IngestUnavailableError(RuntimeError):
    """LLM non configurato o non raggiungibile: l'entry resta pending_ingest."""


def _scripts_module(name: str) -> Any:
    """Import lazy di un modulo nella stessa directory scripts/ (anti circolari)."""
    scripts_dir = str(Path(__file__).parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import importlib

    return importlib.import_module(name)


def _wiki_manager() -> Any:
    return _scripts_module("wiki_manager")


def _registry_home(home: Path | None = None) -> Path:
    if home is not None:
        return home
    return _scripts_module("registry_manager").get_registry_home()


# --- Client LLM (LangChain ChatOpenAI verso endpoint Kimi/Moonshot) ---


def build_completion(
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> CompleteFn:
    """Costruisce la callable di completamento verso Kimi via LangChain.

    Solleva IngestUnavailableError se la API key manca o langchain-openai non
    è installato; gli errori di rete diventano IngestUnavailableError al
    momento della chiamata.
    """
    key = api_key or os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY")
    if not key:
        raise IngestUnavailableError(
            "API key Kimi mancante: imposta KIMI_API_KEY o MOONSHOT_API_KEY"
        )
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise IngestUnavailableError(
            "langchain-openai non installato: pip install langchain langchain-openai"
        ) from e

    chosen_model = model or os.environ.get("KIMI_MODEL") or DEFAULT_MODEL
    llm = ChatOpenAI(
        model=chosen_model,
        base_url=base_url or os.environ.get("KIMI_BASE_URL") or DEFAULT_BASE_URL,
        api_key=key,
        # I modelli kimi-k2.x accettano solo temperature=1; i moonshot-v1
        # accettano qualsiasi valore, quindi 1 è il default sicuro per entrambi.
        temperature=float(os.environ.get("KIMI_TEMPERATURE", "1")),
        timeout=30,
        max_retries=1,
        model_kwargs={"response_format": {"type": "json_object"}},
    )

    def complete(prompt: str) -> str:
        try:
            response = llm.invoke(prompt)
        except Exception as e:
            raise IngestUnavailableError(f"LLM Kimi non raggiungibile: {e}") from e
        content = response.content
        if isinstance(content, list):  # content a blocchi (formato LangChain)
            content = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content)

    return complete


# --- Parsing JSON robusto ---


def _parse_json_object(text: str) -> dict[str, Any]:
    """Estrae il primo oggetto JSON dalla risposta (tollera code fence e testo extra)."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("risposta LLM senza oggetto JSON")
    obj, _end = json.JSONDecoder().raw_decode(cleaned, start)
    if not isinstance(obj, dict):
        raise ValueError("la risposta LLM non è un oggetto JSON")
    return obj


def _complete_json(
    complete: CompleteFn, prompt: str, retries: int = MAX_JSON_RETRIES
) -> dict[str, Any]:
    """Chiama l'LLM fino a ottenere un oggetto JSON valido (1 + `retries` tentativi).

    IngestUnavailableError (rete/config) si propaga subito: ritentare non serve.
    """
    last_error: Exception | None = None
    current_prompt = prompt
    for _attempt in range(retries + 1):
        try:
            return _parse_json_object(complete(current_prompt))
        except IngestUnavailableError:
            raise
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            current_prompt = (
                f"{prompt}\n\nATTENZIONE: la risposta precedente non era un JSON "
                f"valido ({e}). Rispondi SOLO con l'oggetto JSON richiesto, "
                "senza testo aggiuntivo."
            )
    raise ValueError(f"JSON non valido dopo {retries + 1} tentativi: {last_error}")


# --- Ingestione ---


def _ingest_prompt(fields: dict[str, Any], body: str) -> str:
    """Prompt strict per generare il campo router (e migliorare i non documentato)."""
    campi = {key: fields.get(key) for key in IMPROVABLE_FIELDS}
    body_excerpt = (body or "").strip()[:4000]
    return f"""Sei l'agente di ingestione della wiki di agent-registry. Ti viene dato il
wiki entry di una sessione di lavoro di un agente AI. Genera un oggetto JSON con:

- "router": descrizione breve (massimo {ROUTER_MAX_CHARS} caratteri) di cosa è stato
  fatto, scritta per essere riconosciuta da un'AI come pertinente a una richiesta
  futura. Deve essere auto-esplicativa e contenere le parole chiave del lavoro.
- "cosa_fatto", "come_fatto", "problema_risolto": valorizzali SOLO se il valore
  attuale è vuoto o "{NON_DOCUMENTATO}", ricavandoli dal contesto; altrimenti
  restituisci stringa vuota per quel campo.

Rispondi SOLO con l'oggetto JSON, senza testo aggiuntivo.

Campi attuali:
{json.dumps(campi, ensure_ascii=False, indent=2)}

Contesto della sessione:
{body_excerpt}
"""


def _wiki_md_path(home: Path, session_id: str) -> Path:
    """Path del markdown dell'entry (riusa la logica di naming di registry_manager)."""
    rm = _scripts_module("registry_manager")
    filename = rm._session_filename(session_id).removesuffix(".yaml") + ".md"
    return home / "wiki" / filename


def _context_rel(home: Path, session_id: str) -> str:
    """Path relativo del context file se esiste, altrimenti stringa vuota."""
    rel = f"contexts/{session_id}-context.md"
    return rel if (home / rel).exists() else ""


def _pending_result(session_id: str, error: str) -> dict[str, Any]:
    """Risultato di un'ingestione fallita: l'entry resta pending_ingest, dati intatti."""
    return {
        "session_id": session_id,
        "ingested": False,
        "status": "pending_ingest",
        "error": error,
    }


def _schedule_sync(home: Path, message: str) -> None:
    """Git-sync best-effort dopo la scrittura del markdown (mai bloccante)."""
    try:
        _scripts_module("registry_manager")._schedule_git_sync(home, message)
    except Exception:
        pass


def ingest_entry(
    session_id: str,
    home: Path | None = None,
    complete: CompleteFn | None = None,
) -> dict[str, Any]:
    """Ingestisce un wiki entry: genera il router via LLM e aggiorna markdown + DB.

    Non solleva mai eccezioni per problemi LLM: in caso di fallimento
    restituisce un dict con `ingested: False` e l'entry resta pending_ingest
    (markdown e DB intatti).
    """
    wm = _wiki_manager()
    home = _registry_home(home)

    md_path = _wiki_md_path(home, session_id)
    if not md_path.exists():
        return _pending_result(
            session_id, f"wiki entry markdown non trovato: {md_path}"
        )
    try:
        fields, body = wm.parse_entry_markdown(md_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _pending_result(session_id, f"wiki entry non parsabile: {e}")

    if complete is None:
        try:
            complete = build_completion()
        except IngestUnavailableError as e:
            return _pending_result(session_id, str(e))

    try:
        data = _complete_json(complete, _ingest_prompt(fields, body))
    except (IngestUnavailableError, ValueError) as e:
        return _pending_result(session_id, str(e))

    router = str(data.get("router") or "").strip()[:ROUTER_MAX_CHARS]
    if not router:
        return _pending_result(session_id, "l'LLM non ha generato il campo router")

    # Migliora i campi narrativi solo se vuoti o "non documentato".
    for key in IMPROVABLE_FIELDS:
        current = str(fields.get(key) or "").strip()
        proposed = str(data.get(key) or "").strip()
        if proposed and (not current or current == NON_DOCUMENTATO):
            fields[key] = proposed

    fields["router"] = router
    fields["session_id"] = session_id
    if not fields.get("id"):
        fields["id"] = wm.entry_id_for(session_id, home)

    # Il markdown resta la fonte di verità: prima il file, poi il DB.
    wm.write_wiki_entry(session_id, fields, body, home)
    entry_id = wm.upsert_entry(
        session_id,
        {**fields, "context_md": _context_rel(home, session_id), "status": "ok"},
        home=home,
    )
    _schedule_sync(home, f"wiki ingest {session_id}")
    return {
        "session_id": session_id,
        "ingested": True,
        "status": "ok",
        "entry_id": entry_id,
        "router": router,
    }


def pending_session_ids(home: Path | None = None) -> list[str]:
    """Session id delle entry in stato pending_ingest (ordine di inserimento)."""
    wm = _wiki_manager()
    con = wm._connect(_registry_home(home))
    try:
        rows = con.execute(
            "SELECT session_id FROM wiki_entries WHERE status = ? ORDER BY id",
            (wm.STATUS_PENDING,),
        ).fetchall()
        return [str(row["session_id"]) for row in rows]
    finally:
        con.close()


def ingest_pending(
    home: Path | None = None,
    complete: CompleteFn | None = None,
) -> list[dict[str, Any]]:
    """Ingestisce tutte le entry pending_ingest; restituisce un risultato per entry."""
    home = _registry_home(home)
    pending = pending_session_ids(home)
    if not pending:
        return []
    if complete is None:
        try:
            complete = build_completion()
        except IngestUnavailableError as e:
            # LLM non disponibile: tutte le pending restano tali, nessun dato perso.
            return [_pending_result(sid, str(e)) for sid in pending]
    return [ingest_entry(sid, home=home, complete=complete) for sid in pending]


# --- Router query ---


def _rank_prompt(domanda: str, candidates: list[dict[str, Any]]) -> str:
    """Prompt strict per il ranking LLM dei candidati FTS."""
    elenco = [
        {"id": int(c["id"]), "data": str(c.get("data") or ""), "router": str(c.get("router") or "")}
        for c in candidates
    ]
    return f"""Sei il router della wiki di agent-registry: decidi se un lavoro è già
stato svolto in passato. Ti viene data una domanda e una lista di sessioni
candidate (id, data, descrizione router). Rispondi SOLO con un oggetto JSON:

{{"results": [{{"id": <id>, "motivazione": "<perché è pertinente>"}}]}}

Includi SOLO gli id davvero pertinenti alla domanda (anche nessuno: "results": []).
Non inventare id non presenti nella lista.

Domanda: {domanda}

Candidati:
{json.dumps(elenco, ensure_ascii=False, indent=2)}
"""


def format_query_output(result: dict[str, Any]) -> str:
    """Formatta il risultato della query per l'agente richiedente."""
    lines: list[str] = []
    if not result["trovato"]:
        lines.append(result["messaggio"])
        return "\n".join(lines)
    lines.append("Lavori passati pertinenti:")
    for r in result["risultati"]:
        lines.append(
            f"- [#{r['id']}] ({r['session_id']}) {r['router']}"
            + (f" — {r['motivazione']}" if r["motivazione"] else "")
        )
    lines.append("")
    lines.append("Per il dettaglio completo (come_fatto, file_toccati, bug_trovati):")
    lines.append("  registry_manager.py wiki show <id>")
    return "\n".join(lines)


def _fallback_candidates(
    domanda: str, candidates: list[dict[str, Any]], note: str
) -> dict[str, Any]:
    """Fallback D8: ranking LLM non disponibile, restituisce i candidati FTS."""
    result = {
        "domanda": domanda,
        "trovato": True,
        "fallback": True,
        "risultati": [
            {
                "id": int(c["id"]),
                "session_id": str(c["session_id"]),
                "router": str(c.get("router") or ""),
                "motivazione": "",
            }
            for c in candidates
        ],
        "messaggio": "",
    }
    result["messaggio"] = format_query_output(result) + (
        f"\n(ranking LLM non disponibile: {note}; mostrati i candidati della ricerca testuale)"
    )
    return result


def router_query(
    domanda: str,
    home: Path | None = None,
    complete: CompleteFn | None = None,
    max_candidates: int = MAX_QUERY_CANDIDATES,
) -> dict[str, Any]:
    """Risponde a "questo lavoro è già stato fatto?" con pre-filtro FTS + ranking LLM.

    Senza candidati FTS risponde "non risulta svolto in passato" SENZA chiamare
    l'LLM. Con candidati, l'LLM seleziona quelli pertinenti con motivazione;
    se l'LLM non è disponibile ricade sui candidati FTS (fallback D8).
    """
    wm = _wiki_manager()
    home = _registry_home(home)
    candidates = wm.search(domanda, home=home, limit=max_candidates)
    if not candidates:
        return {
            "domanda": domanda,
            "trovato": False,
            "risultati": [],
            "messaggio": (
                "Non risulta svolto in passato: nessuna sessione nella wiki "
                "corrisponde alla domanda."
            ),
        }

    if complete is None:
        try:
            complete = build_completion()
        except IngestUnavailableError as e:
            return _fallback_candidates(domanda, candidates, str(e))

    try:
        data = _complete_json(complete, _rank_prompt(domanda, candidates))
    except (IngestUnavailableError, ValueError) as e:
        return _fallback_candidates(domanda, candidates, str(e))

    by_id = {int(c["id"]): c for c in candidates}
    results: list[dict[str, Any]] = []
    raw_results = data.get("results") or data.get("risultati") or []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        try:
            cid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        entry = by_id.get(cid)
        if entry is None:
            continue  # mai inventare riferimenti: solo id tra i candidati
        results.append(
            {
                "id": cid,
                "session_id": str(entry["session_id"]),
                "router": str(entry.get("router") or ""),
                "motivazione": str(item.get("motivazione") or ""),
            }
        )

    if not results:
        return {
            "domanda": domanda,
            "trovato": False,
            "risultati": [],
            "messaggio": (
                "Non risulta svolto in passato: nessun candidato è pertinente "
                "alla domanda secondo il router."
            ),
        }
    result = {
        "domanda": domanda,
        "trovato": True,
        "risultati": results,
        "messaggio": "",
    }
    result["messaggio"] = format_query_output(result)
    return result


def show_entry(identifier: str, home: Path | None = None) -> dict[str, Any] | None:
    """Entry completa dal DB, per id numerico o session_id; None se non trovata."""
    wm = _wiki_manager()
    con = wm._connect(_registry_home(home))
    try:
        row = None
        if str(identifier).isdigit():
            row = con.execute(
                "SELECT * FROM wiki_entries WHERE id = ?", (int(identifier),)
            ).fetchone()
        if row is None:
            row = con.execute(
                "SELECT * FROM wiki_entries WHERE session_id = ?", (str(identifier),)
            ).fetchone()
        return wm._row_to_dict(row) if row is not None else None
    finally:
        con.close()


# --- CLI ---


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wiki_ingest.py",
        description="Ingestione wiki e router query via LangChain + Kimi.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="ingestisce un wiki entry (genera il router)")
    p.add_argument("session_id")

    sub.add_parser("ingest-pending", help="ingestisce tutte le entry pending_ingest")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point CLI. Exit code != 0 se qualche ingestione non è riuscita."""
    args = _build_parser().parse_args(argv)

    if args.command == "ingest":
        result = ingest_entry(args.session_id)
        if not result["ingested"]:
            print(
                f"[wiki_ingest] {args.session_id}: ingestione non riuscita "
                f"({result['error']}); entry intatta, status pending_ingest.",
                file=sys.stderr,
            )
            return 1
        print(
            f"[wiki_ingest] {args.session_id}: ingerita (entry #{result['entry_id']}).\n"
            f"router: {result['router']}"
        )
        return 0

    # ingest-pending
    results = ingest_pending()
    if not results:
        print("[wiki_ingest] nessuna entry pending_ingest.")
        return 0
    failures = 0
    for result in results:
        if result["ingested"]:
            print(f"[wiki_ingest] {result['session_id']}: ingerita.")
        else:
            failures += 1
            print(
                f"[wiki_ingest] {result['session_id']}: {result['error']}",
                file=sys.stderr,
            )
    print(
        f"[wiki_ingest] {len(results) - failures}/{len(results)} entry ingerite.",
        file=sys.stderr if failures else sys.stdout,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
