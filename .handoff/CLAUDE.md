# CLAUDE.md — Istruzioni Persistenti
> Progetto: agent-registry | Aggiornato: 2026-07-17
> Leggere all'inizio di ogni sessione Claude Code.

## Chi sono / Contesto

`agent-registry` è una **skill** che coordina agenti AI CLI (Claude, Kimi, Gemini,
Codex) che lavorano contemporaneamente sullo stesso progetto: registry condiviso +
lock sui file. Operatore: Giuseppe Di Canosa.

Distribuita su:
- **Tessl** — `spec-driven-development/agent-registry` (public, latest = 0.2.1)
- **GitHub** — https://github.com/GiuseppeDiCanosa/agent-registry (public, MIT)

Questo repo è la **sorgente**. Le copie installate (`.tessl/plugins/...`) sono derivati:
non modificarle mai, si rigenerano con `npx tessl i`.

## Stack & Tool

- Python 3.13, solo stdlib + PyYAML (nessuna dipendenza nuova senza motivo forte)
- pytest 8 — i test girano in **processi separati**, mai in-process (vedi Regole)
- FastAPI + uvicorn per la dashboard (`scripts/webapp/`, porta 8765)
- openspec 1.4.1 con schema custom `spec-as-source`
- `gh` CLI (autenticato come GiuseppeDiCanosa), `npx tessl` per la pubblicazione

## Regole operative

- **La spec è la sorgente, il codice il derivato.** Nessun codice prima della spec.
  Vedi `~/spec-as-source/rules/` per le regole canoniche.
- **`scripts/lock_manager.py` e `scripts/registry_manager.py` sono `targets`** delle
  spec in `openspec/specs/`. Non si toccano senza modificare la spec che li governa
  **nello stesso commit**. Hanno l'header `GENERATED FROM SPEC`.
- **Mai testare la concorrenza in-process.** È l'errore che ha reso la suite 0.1.0
  verde su codice rotto. Usa gli helper di `tests/conftest.py`: `runner.call()`
  (processo che muore), `runner.race_varied()` (N processi con barriera di wall-clock),
  `runner.cli()` (flusso bash reale).
- **Isola sempre via ambiente, mai via monkeypatch**: `AGENT_REGISTRY_PATH`,
  `AGENT_REGISTRY_LOCK_DIR`. La fixture `isolated_env` reindirizza anche `HOME`,
  perché un codice che ignorasse l'override scriverebbe nel Desktop reale (è successo).
- **Prima di pubblicare su Tessl**: pubblicare da una **directory di staging**, mai
  dal repo. `tessl plugin pack` da qui includerebbe `.claude/skills/openspec-*`, che
  sono skill di OpenSpec e non nostre. Vedi `.handoff/PROMPTS.md`.
- **Non gitignorare `openspec/`**: il CI `spec-verification.yml` legge `openspec/specs/`
  e senza spec passerebbe **verde a vuoto**. Le spec non viaggiano nel pacchetto Tessl,
  ma restano nel repo.

## Preferenze di comunicazione

- Lingua: italiano
- Tono: diretto e tecnico. Dire cosa non ha funzionato, non solo cosa ha funzionato.
- **Provare, non ipotizzare**: ogni bug va riprodotto con comandi reali e output
  osservato. Una race va mostrata vincente, non descritta come possibile.
- Confermare prima delle azioni pubbliche/irreversibili (publish, repo pubblici,
  archiviazioni).

## Path importanti

- Sorgente: `~/Claude-Projects/agent-registry`
- Progetto di prova usa-e-getta: `~/Claude-Projects/prova-agent-registry`
- Regole SDD canoniche: `~/spec-as-source/rules/`
- Spec: `openspec/specs/{file-locking,agent-registry}/spec.md`
- Documentazione sessioni: `.handoff/`
