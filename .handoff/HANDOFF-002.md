# HANDOFF-002 — agent-registry

> Data: 2026-07-22 01:40 | Sessione: #2 | Continua da: HANDOFF-001.md
> Progetto: agent-registry | Operatore: Giuseppe + Claude Opus 4.8

---

## 🎯 Goal

La skill `agent-registry` (coordina agenti AI CLI + wiki collettiva) è già corretta,
testata e pubblicata (Tessl `spec-driven-development/agent-registry` latest 0.2.1;
GitHub pubblico, MIT — vedi HANDOFF-001). **Nuovo obiettivo di questa fase:
containerizzare la skill in Docker**, così che la dashboard (e potenzialmente il
runtime degli script) girino in un container riproducibile invece di dipendere dal
Python/deps installati a mano sulla macchina. Deliverable atteso: `Dockerfile` +
orchestrazione (compose) + istruzioni d'uso, con la home registry `~/.agent-registry`
montata come volume e git-sync/wiki funzionanti dentro/attraverso il container.

## ✅ Current Progress

### Completato ✓ (sessione #2 — 22 lug)

- [x] **Diagnosticato e risolto il bug "dashboard vuota"**: la webapp su :8765 mostrava
  sempre "Nessuna sessione corrisponde ai filtri", non mostrava la Wiki e chiedeva di
  configurare il multi-macchina (già configurato). **Causa**: processo avviato con la
  variabile **deprecata** `AGENT_REGISTRY_PATH` puntata a
  `.../CIVICO15/EventoFestaPatronale/10 Anni Civico/.agent-registry/registry.md` — un
  registry di progetto in **formato vecchio single-file**, senza `sessions/`, `wiki/`, git.
- [x] **Riavviata la dashboard sulla home globale corretta** `~/.agent-registry` con
  `AGENT_REGISTRY_HOME`. Verificato via curl: `/api/sync` → `enabled:true`,
  `/api/sessions` → 7 sessioni, `/api/wiki` → 1 entry. (Processo dashboard attuale: PID
  22366, `python3 -m uvicorn main:app --host 127.0.0.1 --port 8765`.)
- [x] **Confermato che il multi-macchina è già configurato**: `~/.agent-registry` ha git
  remote `origin` = `https://github.com/GiuseppeDiCanosa/Agent-Registry-Wiki.git`.
- [x] **Abilitata la distillazione LLM della wiki**: impostata `KIMI_API_KEY` in `~/.zshrc`
  (→ vedi .env/shell, mai nel testo), installate le deps mancanti `langchain` +
  `langchain-openai` (1.4.0). Testato: `wiki ingest claude-1784676545` → entry #2 ingerita
  con router raffinato dall'LLM. Chiave validata e funzionante.
- [x] **Registrata sessione Claude nel registry** (`claude-1784676545`) e **distillato il bug
  della dashboard nella wiki** (entry #2).
- [x] **Salvate 2 memorie globali**: avvio corretto dashboard (`AGENT_REGISTRY_HOME`) e deps
  ingest wiki (`langchain-openai` + chiave).

### In corso / Sospeso (nuovo task Docker — non ancora iniziato)

- [ ] **Scope Docker da definire** (vedi Next Step #1): dashboard-only vs runtime completo vs compose.
- [ ] **Spec SDD del cambiamento Docker** (regola globale: nessun codice prima della spec).
- [ ] `Dockerfile` + `.dockerignore` + compose + doc.

### Ereditato da HANDOFF-001 — stato non verificato in questa sessione

- [ ] Esito sottoagente avversariale / triage bug (S1 next-step #1,#3): **non toccato**.
- [ ] Decisione hermes-agent-creator / agente Kimi vero in Docker (S1 #2): **non toccato**
  — parzialmente rilevante al nuovo task Docker.
- [ ] `tessl plugin unpublish` 0.2.0 (S1 #4): **non verificato**.
- [ ] Change dedicato default `~/Desktop` (S1 #5): superato di fatto — la home in uso ora è
  `~/.agent-registry` (nuovo formato per-sessione), non più `~/Desktop/agent-registry`.
- [ ] Pulizia `~/Claude-Projects/prova-agent-registry` (S1 #6): **non verificato**.

## 💡 What Worked

- **(S2) Leggere l'env del processo vivo per trovare il registry sbagliato**:
  `lsof -nP -iTCP:8765 -sTCP:LISTEN -t` per il PID, poi
  `ps eww -p <PID> | tr ' ' '\n' | grep AGENT_REGISTRY`. Ha mostrato subito che la
  dashboard puntava al registry di progetto via `AGENT_REGISTRY_PATH`. La causa era nell'env
  di avvio, non nei filtri della UI né in una "macchina virtuale".
- **(S2) Confrontare le due home invece di fidarsi della UI**: `~/.agent-registry` (nuovo
  formato: `sessions/`, `wiki/`, `wiki.db`, `.git` con remote) vs la cartella di progetto
  (solo `registry.md` vecchio formato). Il confronto ha reso la diagnosi non discutibile.
- **(S2) Verificare via API, non a occhio**: `curl -s localhost:8765/api/{sync,sessions,wiki}`
  ha confermato oggettivamente enabled/count dopo il riavvio.
- **(S2) Avvio corretto della dashboard**:
  `cd ~/.claude/skills/agent-registry/scripts/webapp && AGENT_REGISTRY_HOME=~/.agent-registry python3 -m uvicorn main:app --host 127.0.0.1 --port 8765`.
  Questo è il comando load-bearing da replicare nel container.
- **(S2) Ingest wiki = chiave + deps insieme**: `KIMI_API_KEY` da sola non basta, serve
  `pip install langchain langchain-openai`. Con entrambe, `wiki ingest <sid>` genera il router LLM.

## ❌ What Didn't Work

- **(S2) `AGENT_REGISTRY_PATH` (deprecata) puntata a un registry di progetto**: è la causa
  radice del bug dashboard. → In Docker **non** usare `AGENT_REGISTRY_PATH`; usare
  `AGENT_REGISTRY_HOME` e montare `~/.agent-registry` come volume.
- **(S2) Riga rotta in `~/.zshrc`**: il comando dell'utente per aggiungere la chiave aveva la
  `"` di chiusura mancante (`export KIMI_API_KEY="sk-...` senza chiudere) → `zsh -n` dava
  `unmatched "`, ogni nuovo terminale sarebbe partito in errore. Corretta chiudendo la virgoletta.
  → Quando si scrivono env con virgolette via `echo >>`, verificare sempre con `zsh -n ~/.zshrc`.
- **(S2) Ingest fallito con solo la chiave**: prima con "API key mancante", poi (dopo aver
  settato la chiave) con "langchain-openai non installato". → Servono **entrambe** le condizioni.
- **(Sicurezza, S2)**: la `KIMI_API_KEY` è transitata in chiaro in chat (incollata nel comando).
  → Valutare rigenerazione della chiave. Non è stato fatto (scelta rimandata all'utente).

## 🚀 Next Steps

1. **Definire lo scope del container** (decisione utente, blocca tutto il resto). Tre opzioni:
   (a) **solo dashboard** FastAPI/uvicorn in container, `~/.agent-registry` montata come
   volume — più semplice e già utile; (b) **runtime completo**: immagine con Python + tutti
   gli script (`registry_manager`/`lock_manager`/`sync_manager`/`wiki_*`) per far girare i
   comandi senza deps locali; (c) **compose** con dashboard + git-sync + volume + env
   `KIMI_API_KEY`. Nodo tecnico da chiarire con l'utente: i **lock sono `fcntl` advisory su
   filesystem** — funzionano tra host e container solo su volume condiviso sullo **stesso host**;
   agenti su macchine diverse si coordinano via git-sync, non via lock. Chiarire se il container
   deve solo *mostrare* (dashboard) o anche *acquisire lock* (runtime agenti).
2. **Scrivere la spec SDD del cambiamento** (regola globale: la spec è la sorgente). Nuovo change
   openspec in `~/Claude-Projects/agent-registry/openspec/`, con `targets:` = i nuovi file Docker.
   Nessun `Dockerfile` prima della spec.
3. **Implementare**: `Dockerfile` (base `python:3.13-slim`, install da
   `scripts/webapp/requirements.txt` + eventualmente `scripts/requirements.txt`), `.dockerignore`,
   `docker-compose.yml` (volume `~/.agent-registry:/data`, `AGENT_REGISTRY_HOME=/data`, porta
   8765, `KIMI_API_KEY` da env host). Header `GENERATED FROM SPEC` se finiscono nei `targets`.
4. **Testare nel container reale** (OrbStack presente, docker 29.4.0): build, run, verificare
   che la dashboard veda le 7 sessioni e la wiki montando il volume, e che `/api/sync` resti
   `enabled`. Provare un `wiki ingest` dentro il container con la chiave passata via env.
5. **Documentare** in `README.md`/`SKILL.md` come avviare via Docker, e aggiornare
   `.handoff/CLAUDE.md` con la regola "in Docker usare `AGENT_REGISTRY_HOME`, mai `AGENT_REGISTRY_PATH`".

## 📎 Context Importante

- **Sorgente vera del progetto**: `~/Claude-Projects/agent-registry` (sotto git). La copia in
  `~/.claude/skills/agent-registry/` è un **derivato installato** — non modificarla, si
  rigenera con `npx tessl i`. Il lavoro Docker va fatto sulla sorgente.
- **Home registry in uso**: `~/.agent-registry` (nuovo formato per-sessione: `sessions/*.yaml`,
  `wiki/*.md`, `wiki.db`, `contexts/`), git remote `Agent-Registry-Wiki` (privato).
- **Deps runtime**: dashboard = `fastapi`, `uvicorn[standard]`, `PyYAML`
  (`scripts/webapp/requirements.txt`); wiki-ingest = `PyYAML`, `langchain==0.3.30`,
  `langchain-openai==0.3.35` (`scripts/requirements.txt`).
- **Credenziali**: `KIMI_API_KEY` in `~/.zshrc` (host) → passarla al container via env, mai nel
  Dockerfile/immagine. Nessun segreto nel repo (pubblico su GitHub).
- **Docker engine**: OrbStack, `docker` v29.4.0, CLI in `~/.orbstack/bin/docker`.
- **Riferimenti**: `.handoff/CLAUDE.md` (regole operative), `.handoff/WORKFLOW.md` (metodo SDD),
  `.handoff/PROMPTS.md` (comandi validati), `~/spec-as-source/rules/` (regole SDD canoniche).
