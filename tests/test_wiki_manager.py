"""Test per il gruppo 4: context log, wiki_manager (DB/FTS/rebuild), flusso end."""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import lock_manager as lm
import registry_manager as rm
import wiki_manager as wm


@pytest.fixture(autouse=True)
def _no_real_legacy(monkeypatch, tmp_path):
    """Impedisce che i test tocchino un eventuale registry legacy reale su Desktop."""
    monkeypatch.setattr(
        rm, "_legacy_registry_path", lambda: tmp_path / "no-legacy" / "registry.md"
    )


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Home del registry in tmp_path, con lock dir coerente."""
    home = tmp_path / "registry-home"
    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(home))
    monkeypatch.delenv("AGENT_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(lm, "LOCK_DIR", home / "locks")
    lm._OPEN_LOCK_FDS.clear()
    yield home
    lm._OPEN_LOCK_FDS.clear()


def _register(tmp_home, session_id="s1"):
    return rm.register_session(
        session_id=session_id,
        provider="Kimi",
        ai_version="2.7",
        working_on="Test wiki",
        space=["src/a.py", "src/b.py"],
    )


# --- 4.1: context log ---


def test_context_log_creates_header_with_metadata(tmp_home):
    _register(tmp_home)
    path = rm.log_context("s1", "prima azione")
    assert path == tmp_home / "contexts" / "s1-context.md"
    content = path.read_text(encoding="utf-8")
    assert content.startswith("# Context sessione s1")
    assert "provider: Kimi" in content
    assert "modello: 2.7" in content
    assert "prima azione" in content


def test_context_log_appends_in_sequence(tmp_home):
    _register(tmp_home)
    rm.log_context("s1", "azione uno")
    rm.log_context("s1", "azione due")
    rm.log_context("s1", "azione tre")
    content = (tmp_home / "contexts" / "s1-context.md").read_text(encoding="utf-8")
    assert content.count("# Context sessione s1") == 1  # header creato una volta sola
    i1 = content.index("azione uno")
    i2 = content.index("azione due")
    i3 = content.index("azione tre")
    assert i1 < i2 < i3  # append in sequenza preservati
    assert content.count("- **") == 3  # ogni entry ha il suo timestamp


def test_context_log_without_session(tmp_home):
    """Context log per sessione sconosciuta: file creato comunque, metadati vuoti."""
    rm.ensure_registry()
    path = rm.log_context("sconosciuta", "entry orfana")
    assert path.exists()
    assert "entry orfana" in path.read_text(encoding="utf-8")


# --- 4.3: upsert, FTS search, rebuild, pending_ingest ---


def test_fts5_available():
    """FTS5 è atteso nella build standard di sqlite3 su macOS (altrimenti fallback LIKE)."""
    assert wm._fts5_available() is True


def test_upsert_and_search(tmp_home):
    wm.upsert_entry(
        "s1",
        {
            "provider": "Kimi",
            "modello": "2.7",
            "data": "2026-07-20",
            "router": "migrazione registry",
            "cosa_fatto": "implementata deduplicazione anagrafiche",
            "bug_trovati": ["race condition nel lock"],
        },
        home=tmp_home,
    )
    wm.upsert_entry(
        "s2",
        {
            "provider": "Claude",
            "data": "2026-07-20",
            "router": "dashboard",
            "cosa_fatto": "rifatta la UI",
        },
        home=tmp_home,
    )
    # Trova per parola in cosa_fatto
    hits = wm.search("deduplicazione", home=tmp_home)
    assert [h["session_id"] for h in hits] == ["s1"]
    # Trova per parola in bug_trovati
    hits = wm.search("race condition", home=tmp_home)
    assert [h["session_id"] for h in hits] == ["s1"]
    assert hits[0]["bug_trovati"] == ["race condition nel lock"]
    # Query senza match
    assert wm.search("inesistente", home=tmp_home) == []


def test_upsert_id_progressivo_e_update(tmp_home):
    id1 = wm.upsert_entry("s1", {"cosa_fatto": "uno"}, home=tmp_home)
    id2 = wm.upsert_entry("s2", {"cosa_fatto": "due"}, home=tmp_home)
    assert (id1, id2) == (1, 2)
    # Update della stessa sessione: id conservato
    again = wm.upsert_entry("s1", {"cosa_fatto": "uno bis"}, home=tmp_home)
    assert again == id1
    hits = wm.search("bis", home=tmp_home)
    assert hits[0]["id"] == id1


def test_pending_ingest_senza_router(tmp_home):
    wm.upsert_entry("s1", {"cosa_fatto": "x"}, home=tmp_home)
    wm.upsert_entry("s2", {"cosa_fatto": "y", "router": "desc"}, home=tmp_home)
    rows = {r["session_id"]: r for r in wm.search("x OR y", home=tmp_home)}
    assert rows["s1"]["status"] == "pending_ingest"
    assert rows["s2"]["status"] == "ok"


def test_rebuild_da_markdown(tmp_home):
    wm.write_wiki_entry(
        "s1",
        {
            "id": 1,
            "session_id": "s1",
            "provider": "Kimi",
            "data": "2026-07-20",
            "router": "prima entry",
            "cosa_fatto": "fatto uno",
            "file_toccati": ["a.py"],
        },
        "body uno",
        home=tmp_home,
    )
    wm.write_wiki_entry(
        "s2",
        {"id": 2, "session_id": "s2", "cosa_fatto": "fatto due"},
        "body due",
        home=tmp_home,
    )
    # Cancella il DB e ricostruisci dai markdown
    db = tmp_home / "wiki.db"
    if db.exists():
        db.unlink()
    count = wm.rebuild(home=tmp_home)
    assert count == 2
    assert db.exists()
    hits = wm.search("fatto", home=tmp_home)
    assert {h["session_id"] for h in hits} == {"s1", "s2"}
    by_sid = {h["session_id"]: h for h in hits}
    assert by_sid["s1"]["id"] == 1
    assert by_sid["s1"]["status"] == "ok"
    assert by_sid["s1"]["file_toccati"] == ["a.py"]
    # s2 senza router -> pending_ingest
    assert by_sid["s2"]["status"] == "pending_ingest"


# --- 4.4: flusso end ---


def test_end_flow_completo(tmp_home):
    _register(tmp_home)
    rm.add_handoff_ref("s1", ".handoff-kimi/HANDOFF-001.md")
    lm.acquire_lock("/tmp/fake/file.py", "s1")
    assert lm.is_locked("/tmp/fake/file.py")["locked"]
    rm.log_context("s1", "analisi del problema")
    rm.log_context("s1", "fix applicato")

    result = rm.end_session(
        "s1",
        router="fix registry wiki",
        cosa="implementato il wiki manager",
        come="sqlite + fts5",
        risolto="mancanza memoria persistente",
        bug=["trigger fts mancante"],
        skill_tool=["skill:agent-registry"],
    )
    assert result["ended"] is True
    assert result["undocumented"] == []

    # Wiki entry markdown con tutti i campi e body = context integrale
    wiki_file = tmp_home / "wiki" / "s1.md"
    assert wiki_file.exists()
    fields, body = wm.parse_entry_markdown(wiki_file.read_text(encoding="utf-8"))
    assert fields["session_id"] == "s1"
    assert fields["provider"] == "Kimi"
    assert fields["modello"] == "2.7"
    assert fields["data"] == fields["data"][:10] and len(fields["data"]) == 10
    assert fields["router"] == "fix registry wiki"
    assert fields["cosa_fatto"] == "implementato il wiki manager"
    # space include anche il path lockato (sync lock↔registry, D5)
    assert fields["file_toccati"] == ["src/a.py", "src/b.py", "/tmp/fake/file.py"]
    assert fields["handoff"] == [".handoff-kimi/HANDOFF-001.md"]
    assert fields["bug_trovati"] == ["trigger fts mancante"]
    assert fields["skill_tool_mcp"] == ["skill:agent-registry"]
    assert isinstance(fields["git_push"], list)
    assert "analisi del problema" in body
    assert "fix applicato" in body

    # DB aggiornato e ricercabile
    hits = wm.search("wiki manager", home=tmp_home)
    assert hits[0]["session_id"] == "s1"
    assert hits[0]["status"] == "ok"
    assert hits[0]["context_md"] == "contexts/s1-context.md"

    # Sessione Finished e lock rilasciati
    agent = rm.find_agent("s1")
    assert agent["status"] == "Finished"
    assert agent["do_not_touch"] == []
    assert not lm.is_locked("/tmp/fake/file.py")["locked"]


def test_end_flow_campi_non_documentati(tmp_home):
    _register(tmp_home, "s2")
    rm.log_context("s2", "lavoro vario")
    result = rm.end_session("s2")
    assert result["ended"] is True
    assert set(result["undocumented"]) == {
        "cosa_fatto",
        "come_fatto",
        "problema_risolto",
        "bug_trovati",
        "skill_tool_mcp",
    }
    fields, _ = wm.parse_entry_markdown(
        (tmp_home / "wiki" / "s2.md").read_text(encoding="utf-8")
    )
    assert fields["cosa_fatto"] == "non documentato"
    assert fields["bug_trovati"] == ["non documentato"]
    # Segnalato in issues della sessione
    agent = rm.find_agent("s2")
    assert "campi non documentati" in agent["issues"]
    assert agent["status"] == "Finished"
    # Senza router -> pending_ingest
    con_rows = wm.search("lavoro OR documentato", home=tmp_home)
    assert con_rows[0]["status"] == "pending_ingest"


def test_end_session_sconosciuta(tmp_home):
    rm.ensure_registry()
    result = rm.end_session("mai-esistita")
    assert result["ended"] is False
    assert "non trovata" in result["error"]


# --- 4.1/4.4 via CLI ---


def test_cli_context_log_e_end(tmp_home, capsys):
    _register(tmp_home, "s3")
    assert rm.main(["context", "log", "s3", "entry da cli"]) == 0
    content = (tmp_home / "contexts" / "s3-context.md").read_text(encoding="utf-8")
    assert "entry da cli" in content

    assert (
        rm.main(["end", "s3", "--router", "test cli", "--cosa", "fatto via cli"]) == 0
    )
    out = capsys.readouterr().out
    assert "Finished" in out
    assert (tmp_home / "wiki" / "s3.md").exists()
    assert rm.find_agent("s3")["status"] == "Finished"

    assert rm.main(["wiki", "rebuild"]) == 0
    out = capsys.readouterr().out
    assert "1 entry" in out
