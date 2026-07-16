# HANDOFF-001 — agent-registry
> Data: 2026-07-17 00:55 | Sessione: #1 | Continua da: —
> Progetto: agent-registry | Operatore: Giuseppe + Claude Opus 4.8

---

## 🎯 Goal

Rendere `agent-registry` una skill che fa davvero ciò per cui esiste: coordinare
agenti AI CLI di provider diversi (Claude, Kimi, Gemini, Codex) che lavorano
contemporaneamente sullo stesso progetto, senza sovrascriversi. La 0.1.0 pubblicata
su Tessl aveva un difetto che ne annullava lo scopo. Deliverable: versione corretta,
verificata con test che esercitano il flusso reale, pubblicata su Tessl e GitHub.

## ✅ Current Progress

### Completato ✓

- [x] **Review della 0.1.0**: due difetti critici riprodotti con la CLI documentata
  - lock rubabile: B acquisiva un lock valido di A dopo 2s (timeout 120s)
  - registro: 8 agenti concorrenti → 8 "successi", **1** sopravvissuto
  - la suite 0.1.0 era **15/15 verde** su questo codice
- [x] **Progetto sorgente**: `~/Claude-Projects/agent-registry`, 10 commit, baseline 0.1.0 come primo commit (il prima/dopo è ispezionabile)
- [x] **Infra spec-as-source**: schema openspec custom, `scripts/check-*.sh`, `verify.sh`, CI `spec-verification.yml` (pytest), pre-commit
- [x] **Spec**: 17 requisiti / 42 scenari in `openspec/specs/{file-locking,agent-registry}/spec.md` — scritte **prima** del codice
- [x] **Test cross-process**: 59 test in processi che terminano davvero. Da **20 rossi** sulla 0.1.0 a **59/59 verdi**
- [x] **`scripts/lock_manager.py` riscritto** — stato nel contenuto del file, flock solo sulla sezione critica
- [x] **`scripts/registry_manager.py` riscritto** — RMW dentro flock su `registry.md.lock` (file dedicato, mai rinominato)
- [x] **Protocollo auto-descrittivo** nel registry, rigenerato a ogni scrittura, auto-riparante (richiesta esplicita in sessione)
- [x] **`work-review.md`** requisito-per-requisito con evidenza `file:riga` verificata
- [x] **Change archiviato** + `targets:`/`Purpose` ripristinati dopo l'archive
- [x] **Pubblicato su Tessl**: `spec-driven-development/agent-registry` — **latest = 0.2.1**, public, moderazione passata
- [x] **Pubblicato su GitHub**: https://github.com/GiuseppeDiCanosa/agent-registry — public, MIT
- [x] **Refuso del nome corretto**: creato workspace `spec-driven-development`; quello vecchio `spec-driven-devlopment` **archiviato** (0.1.0 non più installabile — verificato)
- [x] **Prova end-to-end** in `~/Claude-Projects/prova-agent-registry`: due agenti si contendono `src/auth.py`, il secondo viene bloccato (exit 1), `finish` rilascia il lock, il lock altrui resta intatto

### In corso / Sospeso

- [ ] **Sottoagente avversariale (Claude, general-purpose)** — lanciato in background, esito **non ancora arrivato**. Doveva: (fase 1) dedurre il protocollo dal **solo registry**, senza leggere SKILL.md, per testare la premessa che le regole viaggino col file; (fase 2) caccia ai bug avversariale con riproduzioni. Scenario lasciato vivo: sessione Kimi `OnWorking` con `src/oauth.py` e `src/auth.py` in `do_not_touch`.
- [ ] **hermes-agent-creator** (`~/VibeCoding/hermes-agent-creator`) — richiesto dall'utente per generare l'agente con un provider **vero** (Kimi in Docker) invece di simularlo. **Bloccato: mancano TUTTI i prerequisiti** (vedi What Didn't Work). Non toccato.
- [ ] **0.2.0 ancora pubblicata** su Tessl: non è più `latest`, ma resta pinnabile e contiene `openspec/specs/` nel pacchetto. Da valutare `tessl plugin unpublish`.
- [ ] **Default `~/Desktop/agent-registry/`**: mono-macchina, mono-progetto, esposto a iCloud (copie in conflitto sul file autorevole). Fuori scope dal change fatto, va affrontato in uno dedicato — vedi *Open Questions* in `openspec/changes/archive/2026-07-16-fix-cross-process-coordination/design.md`.
- [ ] **`~/Claude-Projects/prova-agent-registry`** — progetto di prova usa-e-getta, cancellabile.

## 💡 What Worked

- **Riprodurre i difetti prima di parlarne**: due prove con la CLI documentata hanno
  trasformato "mi sembra sbagliato" in un fatto non discutibile. È ciò che ha giustificato
  l'intera riscrittura.
- **La separazione che regge tutto il design**: lo *stato* (chi possiede il path) sta nel
  **contenuto di un file**, che sopravvive alla morte del processo; la *mutua esclusione*
  durante l'aggiornamento è data da `flock`, tenuto solo per la sezione critica dentro un
  processo vivo. L'errore della 0.1.0 non era "usare flock", era **chiedere a flock di essere
  lo stato**. Stessa syscall, ruolo opposto → il verdetto dipende dalla *durata richiesta*.
- **Vincolo load-bearing: il lock file non si cancella mai** (il rilascio ne azzera il
  contenuto). Un `unlink` sostituirebbe l'inode e due processi flockerebbero oggetti diversi
  — lo stesso identico errore da un'altra porta.
- **Barriera di wall-clock condivisa** in `tests/conftest.py::race_varied()`: senza, i
  processi partono scaglionati dallo startup di Python (~50ms) e si serializzano da soli,
  nascondendo la race. Con la barriera, `test_concurrent_acquire_elects_single_winner`
  osserva la contesa vera.
- **`HOME` finta nella fixture `isolated_env`**: rete di sicurezza, non dettaglio. Un codice
  che ignora l'override ricade sul default e scrive nella home reale (è successo, vedi sotto).
- **Mutation test dopo aver corretto un test**: reintrodotto il bug in `_fmt_list` per
  verificare che il test riformulato sappia ancora fallire. Un test che non fallisce mai non
  verifica nulla.
- **Pubblicare da una directory di staging**: unico modo di controllare cosa viaggia, dato
  che non esiste `.tesslignore`. Vedi `PROMPTS.md [PUB-001]`.
- **Correggere il design a metà strada invece di forzare il codice**: `design.md` prevedeva
  `O_EXCL` + `os.link()`; implementando è emerso il buco. Aggiornati design.md e tasks.md.
  È la disciplina spec che fa il suo lavoro.

## ❌ What Didn't Work

- **Design `O_EXCL` + `os.link()` con verifica `st_ino`/`st_mtime_ns`** (previsto in design.md,
  **scartato**): dà un vincitore unico sull'acquisizione ma **non risolve il takeover di uno
  stale**. `unlink()` agisce sul *nome*, non sull'inode: non esiste "cancella solo se è ancora
  quello che ho letto", e fra il controllo e l'unlink non c'è atomicità. → Non ritentare questa
  strada.
- **Test in-process** (l'errore originale della 0.1.0): la mutua esclusione era garantita da
  `_OPEN_LOCK_FDS`, un dict in RAM, mai dal filesystem. L'unico test cross-process teneva il
  worker vivo con `sleep` — l'unico caso in cui flock regge, cioè **l'opposto dell'uso reale**.
  Il test che avrebbe trovato il bug era scritto in modo da evitarlo. → **Mai testare la
  concorrenza in-process.**
- **Il mio test run ha sporcato il Desktop reale**: la 0.1.0 aveva `LOCK_DIR` risolta
  all'import, quindi ignorava `AGENT_REGISTRY_LOCK_DIR` e ha scritto 18 lock in
  `~/Desktop/agent-registry/` (cartella che prima non esisteva). Rimossa, e fixture blindata
  con `HOME` finta. → **Nesso causale importante: il design non testabile ha prodotto i test
  ciechi** (con LOCK_DIR congelata, l'unico isolamento possibile era il monkeypatch, che
  funziona solo in-process).
- **`tessl plugin pack` dal repo includeva `.claude/skills/openspec-*` e `.claude/commands/opsx/*`**:
  skill di OpenSpec, **non nostre**. Pubblicarle le avrebbe ridistribuite e iniettate nel
  progetto di chi installa. Non esiste `.tesslignore`. → Pubblicare **solo** da staging.
- **`tile.json` creato a mano**: ridondante e ignorato. `.tessl-plugin/plugin.json` è
  autoritativo, `tile.json` è sintetizzato al pack. → Non crearlo.
- **0.2.0 pubblicata troppo presto**: l'ho pubblicata con `openspec/specs/` dentro, prima che
  l'utente chiedesse di escluderle → è servita una 0.2.1. → Chiedere **cosa deve viaggiare**
  prima di pubblicare, non dopo.
- **`latest` non si aggiorna subito**: appena pubblicata la 0.2.1, un `npx tessl i` non pinnato
  scaricava ancora la 0.2.0 (moderazione). Si è sistemato da solo in pochi minuti. → Verificare
  con un'installazione pulita, non fidarsi del solo `plugin info`.
- **`uvicorn` e `fastapi` non erano installati**: la webapp della 0.1.0 non era mai stata
  avviata su questa macchina. Il primo tentativo di lancio è fallito in silenzio (log:
  `No module named uvicorn`).
- **`cut --output-delimiter` è GNU**: su macOS non esiste, usare `awk`. (Errore mio in un
  comando di ispezione, non un bug della skill.)
- **hermes-agent-creator inutilizzabile**: mancano **tutti** i prerequisiti — `hermes` non è
  nel PATH, Docker/OrbStack non risponde, `~/.hermes/.env` non esiste (quindi niente
  `KIMI_API_KEY`), `.venv` assente. Non è un pezzo mancante: è l'intero setup.

## 🚀 Next Steps

1. **Recuperare l'esito del sottoagente avversariale** (lanciato in background in questa
   sessione, esito mai arrivato). Se la sessione è chiusa e l'esito è perso, **rilanciarlo**:
   deve dedurre il protocollo dal solo `.agent-registry/registry.md` di
   `~/Claude-Projects/prova-agent-registry` senza leggere SKILL.md, poi cercare bug con
   riproduzioni reali. Lo scenario è già vivo (Kimi `OnWorking`, `src/oauth.py` bloccato).
2. **Decidere su hermes-agent-creator**: se si vuole un agente Kimi vero, serve prima il setup
   (avviare Docker/OrbStack, creare `.venv`, installare Hermes, mettere `KIMI_API_KEY` in
   `~/.hermes/.env`). Decisione dell'utente: coinvolge una chiave API.
3. **Triage dei bug trovati dal sottoagente**: quelli che rompono la garanzia centrale (due
   owner, aggiornamenti persi) → nuovo change SDD. Cosmetici → backlog.
4. **Valutare `npx tessl plugin unpublish --plugin spec-driven-development/agent-registry@0.2.0`**
   (contiene le spec nel pacchetto; innocua ma pinnabile).
5. **Change dedicato per il default `~/Desktop`**: valutare `.agent-registry/` nella root del
   progetto. Attenzione: cambia il comportamento per gli utenti esistenti.
6. **Pulire `~/Claude-Projects/prova-agent-registry`** quando non serve più.

## 📎 Context Importante

- **Credenziali**: nessuna nel repo. `KIMI_API_KEY` andrebbe in `~/.hermes/.env` → non esiste ancora.
- **Il repo è pubblico**: prima di committare, ricordare che tutto è visibile.
- **Riferimenti**: `.handoff/CLAUDE.md` (regole operative), `.handoff/WORKFLOW.md` (metodo SDD
  con le trappole trovate), `.handoff/PROMPTS.md` (comandi validati, riproduzione dei difetti).
- **Decisione architetturale chiave**, motivata in
  `openspec/changes/archive/2026-07-16-fix-cross-process-coordination/design.md`: stato nel
  contenuto del file, flock solo per la sezione critica, lock file mai cancellati.
- **Limiti dichiarati e non risolti**: i lock restano **advisory** (nessuno impedisce
  fisicamente la scrittura); i lock file non si cancellano mai (un file vuoto per path);
  NFS non supportato.
