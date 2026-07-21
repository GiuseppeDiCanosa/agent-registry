"""Test per il gruppo 5: wiki_ingest (LangChain + Kimi) con LLM sempre mockato.

Nessuna chiamata di rete: l'LLM è iniettato come callable `prompt -> str`.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import registry_manager as rm
import wiki_ingest as wi
import wiki_manager as wm


@pytest.fixture(autouse=True)
def _no_real_legacy(monkeypatch, tmp_path):
    """Impedisce che i test tocchino un eventuale registry legacy reale su Desktop."""
    monkeypatch.setattr(
        rm, "_legacy_registry_path", lambda: tmp_path / "no-legacy" / "registry.md"
    )


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Home del registry in tmp_path, senza credenziali Kimi nell'ambiente."""
    home = tmp_path / "registry-home"
    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(home))
    monkeypatch.delenv("AGENT_REGISTRY_PATH", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    return home


def _write_entry(tmp_home, session_id="s1", router="", **extra):
    """Scrive un wiki entry markdown e fa l'upsert nel DB (come il flusso end)."""
    fields = {
        "session_id": session_id,
        "provider": "Kimi",
        "modello": "2.7",
        "data": "2026-07-20",
        "router": router,
        "cosa_fatto": "implementata deduplicazione anagrafiche",
        "come_fatto": "non documentato",
        "problema_risolto": "non documentato",
        "file_toccati": ["src/a.py"],
        "bug_trovati": ["race condition nel lock"],
        **extra,
    }
    fields["id"] = wm.entry_id_for(session_id, tmp_home)
    wm.write_wiki_entry(session_id, fields, "contesto della sessione", home=tmp_home)
    wm.upsert_entry(session_id, fields, home=tmp_home)
    return fields


def _complete_router(router="deduplicazione anagrafiche clienti con match fuzzy"):
    """Callable mock che restituisce un JSON di ingestione valido."""

    def complete(prompt: str) -> str:
        return json.dumps(
            {
                "router": router,
                "cosa_fatto": "",
                "come_fatto": "pipeline python con rapidfuzz",
                "problema_risolto": "duplicati in anagrafica",
            }
        )

    return complete


# --- 5.1: ingestione ---


def test_ingest_genera_router_e_aggiorna_markdown_e_db(tmp_home):
    _write_entry(tmp_home)
    result = wi.ingest_entry("s1", home=tmp_home, complete=_complete_router())
    assert result["ingested"] is True
    assert result["status"] == "ok"
    assert result["router"] == "deduplicazione anagrafiche clienti con match fuzzy"

    # Markdown (fonte di verità) aggiornato
    fields, body = wm.parse_entry_markdown(
        (tmp_home / "wiki" / "s1.md").read_text(encoding="utf-8")
    )
    assert fields["router"] == result["router"]
    assert "contesto della sessione" in body  # body preservato
    # Campi "non documentato" migliorati, quelli già valorizzati intatti
    assert fields["come_fatto"] == "pipeline python con rapidfuzz"
    assert fields["problema_risolto"] == "duplicati in anagrafica"
    assert fields["cosa_fatto"] == "implementata deduplicazione anagrafiche"

    # DB aggiornato e ricercabile via FTS sul nuovo router
    hits = wm.search("match fuzzy", home=tmp_home)
    assert hits[0]["session_id"] == "s1"
    assert hits[0]["status"] == "ok"
    assert hits[0]["router"] == result["router"]


def test_ingest_router_troncato_a_300_caratteri(tmp_home):
    _write_entry(tmp_home)
    result = wi.ingest_entry("s1", home=tmp_home, complete=_complete_router("x" * 400))
    assert result["ingested"] is True
    assert len(result["router"]) == 300


def test_ingest_entry_senza_markdown(tmp_home):
    rm.ensure_registry()
    result = wi.ingest_entry("mai-esistita", home=tmp_home, complete=_complete_router())
    assert result["ingested"] is False
    assert "non trovato" in result["error"]


# --- 5.2: fallback ---


def test_ingest_key_mancante_entry_pending_e_dati_intatti(tmp_home):
    before = _write_entry(tmp_home)
    md_before = (tmp_home / "wiki" / "s1.md").read_text(encoding="utf-8")
    # Nessuna API key in env (fixture) e nessun complete iniettato
    result = wi.ingest_entry("s1", home=tmp_home)
    assert result["ingested"] is False
    assert result["status"] == "pending_ingest"
    assert "API key" in result["error"]
    # Markdown intatto
    assert (tmp_home / "wiki" / "s1.md").read_text(encoding="utf-8") == md_before
    # DB intatto e ancora pending_ingest
    hits = wm.search("deduplicazione", home=tmp_home)
    assert hits[0]["status"] == "pending_ingest"
    assert hits[0]["cosa_fatto"] == before["cosa_fatto"]


def test_ingest_llm_non_raggiungibile_entry_pending(tmp_home):
    _write_entry(tmp_home)
    md_before = (tmp_home / "wiki" / "s1.md").read_text(encoding="utf-8")

    def complete_giù(prompt: str) -> str:
        raise wi.IngestUnavailableError("LLM Kimi non raggiungibile: timeout")

    result = wi.ingest_entry("s1", home=tmp_home, complete=complete_giù)
    assert result["ingested"] is False
    assert result["status"] == "pending_ingest"
    assert (tmp_home / "wiki" / "s1.md").read_text(encoding="utf-8") == md_before
    assert wm.search("deduplicazione", home=tmp_home)[0]["status"] == "pending_ingest"


def test_ingest_json_malformato_retry_poi_successo(tmp_home):
    _write_entry(tmp_home)
    calls = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) < 3:
            return "non sono JSON"
        return json.dumps({"router": "router dopo i retry"})

    result = wi.ingest_entry("s1", home=tmp_home, complete=complete)
    assert result["ingested"] is True
    assert result["router"] == "router dopo i retry"
    assert len(calls) == 3  # 1 tentativo + 2 retry


def test_ingest_json_sempre_malformato_pending_e_dati_intatti(tmp_home):
    _write_entry(tmp_home)
    md_before = (tmp_home / "wiki" / "s1.md").read_text(encoding="utf-8")
    calls = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        return "nessun json qui"

    result = wi.ingest_entry("s1", home=tmp_home, complete=complete)
    assert result["ingested"] is False
    assert result["status"] == "pending_ingest"
    assert len(calls) == 3  # 1 + MAX_JSON_RETRIES
    assert (tmp_home / "wiki" / "s1.md").read_text(encoding="utf-8") == md_before
    assert wm.search("deduplicazione", home=tmp_home)[0]["status"] == "pending_ingest"


def test_ingest_json_in_code_fence(tmp_home):
    _write_entry(tmp_home)

    def complete(prompt: str) -> str:
        return 'Ecco il JSON:\n```json\n{"router": "router da fence"}\n```'

    result = wi.ingest_entry("s1", home=tmp_home, complete=complete)
    assert result["ingested"] is True
    assert result["router"] == "router da fence"


# --- 5.1: ingest_pending ---


def test_ingest_pending_processa_solo_le_pending(tmp_home):
    _write_entry(tmp_home, "s1")  # pending (router vuoto)
    _write_entry(tmp_home, "s2", router="già ingerita")  # ok
    _write_entry(tmp_home, "s3")  # pending
    prompts = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps({"router": "router generato"})

    results = wi.ingest_pending(home=tmp_home, complete=complete)
    assert {r["session_id"] for r in results} == {"s1", "s3"}  # s2 non processata
    assert all(r["ingested"] for r in results)
    assert len(prompts) == 2
    assert wi.pending_session_ids(home=tmp_home) == []


def test_ingest_pending_llm_non_disponibile_nessuna_perdita(tmp_home):
    _write_entry(tmp_home, "s1")
    _write_entry(tmp_home, "s2")
    results = wi.ingest_pending(home=tmp_home)  # no key, no complete
    assert len(results) == 2
    assert all(not r["ingested"] for r in results)
    assert set(wi.pending_session_ids(home=tmp_home)) == {"s1", "s2"}


# --- 5.3: router query ---


def test_query_senza_candidati_fts_non_risulta_e_llm_non_chiamato(tmp_home):
    _write_entry(tmp_home, router="deduplicazione anagrafiche")
    calls = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        return "{}"

    result = wi.router_query("deploy su kubernetes", home=tmp_home, complete=complete)
    assert result["trovato"] is False
    assert "non risulta svolto in passato" in result["messaggio"].lower()
    assert calls == []  # LLM mai chiamato senza candidati


def test_query_con_candidati_ranking_llm(tmp_home):
    _write_entry(tmp_home, "s1", router="deduplicazione anagrafiche clienti")
    _write_entry(tmp_home, "s2", router="deduplicazione prodotti catalogo", cosa_fatto="dedup prodotti")
    captured = []

    def complete(prompt: str) -> str:
        captured.append(prompt)
        return json.dumps(
            {"results": [{"id": 1, "motivazione": "stessa dedup su anagrafiche"}]}
        )

    result = wi.router_query(
        "deduplicazione anagrafiche", home=tmp_home, complete=complete
    )
    assert result["trovato"] is True
    assert len(captured) == 1
    # Il prompt contiene domanda e candidati (id + router)
    assert "deduplicazione anagrafiche" in captured[0]
    assert '"id": 1' in captured[0]
    # Output: id + router + motivazione + istruzione per il dettaglio
    assert result["risultati"] == [
        {
            "id": 1,
            "session_id": "s1",
            "router": "deduplicazione anagrafiche clienti",
            "motivazione": "stessa dedup su anagrafiche",
        }
    ]
    assert "#1" in result["messaggio"]
    assert "wiki show" in result["messaggio"]


def test_query_llm_scarta_tutti_i_candidati(tmp_home):
    _write_entry(tmp_home, "s1", router="fix typo readme")

    def complete(prompt: str) -> str:
        return json.dumps({"results": []})

    result = wi.router_query("fix typo readme", home=tmp_home, complete=complete)
    assert result["trovato"] is False
    assert "non risulta svolto in passato" in result["messaggio"].lower()


def test_query_llm_non_disponibile_fallback_candidati_fts(tmp_home):
    _write_entry(tmp_home, "s1", router="deduplicazione anagrafiche")

    def complete_giù(prompt: str) -> str:
        raise wi.IngestUnavailableError("timeout")

    result = wi.router_query(
        "deduplicazione anagrafiche", home=tmp_home, complete=complete_giù
    )
    assert result["trovato"] is True
    assert result["fallback"] is True
    assert result["risultati"][0]["session_id"] == "s1"
    assert "ranking LLM non disponibile" in result["messaggio"]


def test_query_ignora_id_inventati_dall_llm(tmp_home):
    _write_entry(tmp_home, "s1", router="deduplicazione anagrafiche")

    def complete(prompt: str) -> str:
        return json.dumps({"results": [{"id": 999, "motivazione": "inventato"}]})

    result = wi.router_query(
        "deduplicazione anagrafiche", home=tmp_home, complete=complete
    )
    assert result["trovato"] is False  # id non tra i candidati: scartato


# --- 5.3: show ---


def test_show_entry_per_id_e_session_id(tmp_home):
    _write_entry(tmp_home, "s1", router="desc")
    by_id = wi.show_entry("1", home=tmp_home)
    by_sid = wi.show_entry("s1", home=tmp_home)
    assert by_id == by_sid
    assert by_id["session_id"] == "s1"
    assert by_id["file_toccati"] == ["src/a.py"]
    assert wi.show_entry("inesistente", home=tmp_home) is None


# --- 5.1/5.3 via CLI ---


def test_cli_wiki_ingest_query_show(tmp_home, capsys):
    _write_entry(tmp_home, "s1")

    # CLI wiki_ingest.py ingest con LLM mockato via monkeypatch non possibile:
    # qui si verifica il fallimento pulito senza API key (exit != 0, dati intatti).
    assert wi.main(["ingest", "s1"]) == 1
    err = capsys.readouterr().err
    assert "pending_ingest" in err
    assert wm.search("deduplicazione", home=tmp_home)[0]["status"] == "pending_ingest"

    # Ingestione via funzione con mock, poi query e show da CLI registry_manager.
    wi.ingest_entry("s1", home=tmp_home, complete=_complete_router())
    assert rm.main(["wiki", "query", "match fuzzy"]) == 0
    out = capsys.readouterr().out
    # Senza candidati FTS -> "non risulta" (qui il router contiene 'match fuzzy'...)
    # oppure ranking: in assenza di API key cade sul fallback FTS. In entrambi i
    # casi la CLI non fallisce e menziona l'entry o il non-risulta.
    assert "non risulta" in out.lower() or "#1" in out

    assert rm.main(["wiki", "show", "s1"]) == 0
    out = capsys.readouterr().out
    shown = json.loads(out)
    assert shown["session_id"] == "s1"
    assert shown["router"].startswith("deduplicazione")

    assert rm.main(["wiki", "show", "inesistente"]) == 1
