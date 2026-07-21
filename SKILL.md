---
name: agent-registry
description: >
  Coordina multipli agenti AI CLI (Kimi, Claude, Gemini, OpenAI, ecc.) che lavorano
  contemporaneamente sullo stesso progetto e mantiene una memoria collettiva (wiki)
  di tutte le sessioni passate. Usa questa skill SEMPRE quando lavori in
  parallelo con altre AI CLI, quando devi salvare lo stato di una sessione condivisa,
  quando vuoi evitare sovrascritture su file toccati da altri agenti, quando devi
  chiedere "questo lavoro è già stato fatto? questo bug si è già visto?", o quando
  l'utente parla di "multi-tap", "registry agenti", "coordination", "lock file",
  "handoff condiviso", "wiki delle sessioni", "chi sta lavorando su cosa" o
  "evitare che le AI si pestino i piedi".
---

# Agent Registry — Coordination Multi-Agent + Wiki delle sessioni

Questa skill permette a più agenti AI CLI di lavorare sullo stesso progetto senza
sovrascriversi a vicenda, e dà a ogni agente una **memoria collettiva**: ogni
sessione chiusa lascia un wiki entry interrogabile ("questo lavoro è già stato
fatto? come? che bug sono emersi?").

Il registry condiviso dice:

- chi sta lavorando (provider, versione, sessione, progetto, branch git)
- su cosa sta lavorando (`working on`, todo passato/presente/futuro)
- quali file sta toccando (`space`)
- quali file sono bloccati (`do_not_touch` locks, sempre sincronizzati coi lock reali)
- quali problemi ha riscontrato (`issues`)
- quali handoff ha salvato (`handoff`)

La wiki aggiunge, per ogni sessione passata: cosa è stato fatto, come, file
toccati, bug trovati, skill/tool/MCP invocati, push git e handoff.

Tutto vive nella home globale:

```
~/.agent-registry/
├── registry.md                     # vista renderizzata (frontmatter + tabella)
├── sessions/<session_id>.yaml      # fonte di verità: un file per sessione
├── contexts/<session_id>-context.md  # diario di sessione (context file)
├── locks/<hash>.lock               # lock filesystem (locali, gitignored)
├── wiki/<session_id>.md            # wiki entry distillati (fonte di verità)
├── wiki.db                         # indice SQLite locale (gitignored, ricostruibile)
└── sync-status.json                # stato del git-sync (gitignored)
```

La home può essere un **repo git privato** con auto-sync multi-macchina
(vedi sezione Git-sync). C'è anche una **web-app locale** in Python + FastAPI
per monitorare e gestire lo stato in tempo reale.

> Nei comandi di esempio si usa `python`; se non è disponibile usa `python3`
> oppure il venv del progetto (es. `.venv/bin/python`).

---

## Regola d'oro

> **Leggi il registry prima di toccare qualsiasi file. Non modificare mai un file che è nel campo `do_not_touch` di un altro agente.**

---

## Home del registry e migrazione

La home di default è `~/.agent-registry/`. L'ordine di risoluzione è:

1. env `AGENT_REGISTRY_HOME` (override generale);
2. env `AGENT_REGISTRY_PATH` (**deprecata**: punta al file `registry.md` come
   nella vecchia versione; la home è derivata dalla directory genitore e viene
   emesso un `DeprecationWarning`);
3. default `~/.agent-registry/`.

**Migrazione automatica**: se esiste il vecchio registry
`~/Desktop/agent-registry/registry.md` e la nuova home non esiste ancora, alla
prima operazione i dati vengono migrati nel nuovo formato per-sessione, la vista
viene rigenerata e il vecchio file su Desktop riceve un header
`DEPRECATO — migrato a ~/.agent-registry` (non viene più aggiornato, ma resta
intatto come backup).

---

## Flusso di sessione (obbligatorio)

Ogni agente che carica questa skill DEVE seguire questo flusso.

### 1. Leggi il registry

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py status
```

Mostra la tabella delle sessioni (SESSION ID, PROVIDER, PROGETTO, WORKING ON,
STATUS, ETÀ) più lo stato del git-sync. Filtri opzionali:
`--status OnWorking|Stop|Finished|Killed`, `--provider <nome>`,
`--project <nome>`.

Identifica quali file/aree sono `do_not_touch` per altri agenti con status
`OnWorking` (leggendo `registry.md` nella home o con `show`).

### 2. Registra la sessione

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py register \
  "kimi-$(date +%s)" "Kimi" "2.7" "Refactoring modulo auth" \
  --space "src/auth.py,src/oauth.py" --todo-present "analisi codice,scrittura test"
```

Argomenti posizionali: `session_id`, `provider`, `ai_version`, `working_on`.
Flag opzionali: `--space` e `--todo-present` (liste CSV).
La registrazione cattura automaticamente `pid`, `cmdline`, `project`
(directory corrente) e `git_branch` (vuota se non è un repo git).

### 3. Interroga la wiki PRIMA di iniziare il task

Chiediti: *questo lavoro è già stato fatto? questo bug si è già visto?*

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py wiki query \
  "refactoring del modulo auth con gestione token OAuth"
```

Il router fa un pre-filtro full-text (FTS5 su `router`/`cosa_fatto`/
`bug_trovati`) e poi un ranking LLM sui candidati, restituendo `id` +
descrizione + motivazione. Se non ci sono candidati risponde "non risulta
svolto in passato" senza chiamare l'LLM. Se trova qualcosa di pertinente,
leggi il dettaglio completo (come_fatto, file_toccati, bug_trovati):

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py wiki show 3
# oppure per session_id:
python .agents/skills/agent-registry/scripts/registry_manager.py wiki show "claude-1720000000"
```

### 4. Lavora, loggando nel context file (LoopVerifyContext)

Dopo ogni azione rilevante — prompt ricevuti, skill/tool invocati, ricerche
online, decisioni prese, problemi riscontrati — appendi una riga al tuo
context file:

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py context log \
  "kimi-1626451200" "Ricevuto prompt: refactoring auth. Invocata skill agent-registry; lock su src/auth.py."
```

Il file `contexts/<session_id>-context.md` viene creato al primo log con i
metadati della sessione e cresce con append atomico (flock + fsync): sopravvive
a qualsiasi crash.

**LoopVerifyContext** (procedura disciplinare D9 — dipende da te, non da hook
automatici):

1. dopo ogni scrittura significativa del context, **rileggi il file** e
   verifica che quanto scritto corrisponda a quanto effettivamente fatto;
2. se è incompleto o incoerente, aggiornalo e ripeti la verifica;
3. se la scrittura/verifica è ancora in corso quando arriva un nuovo prompt,
   **chiedi all'utente di attendere** e completala prima di proseguire.

### 5. Lock prima di modificare i file

Prima di modificare un file, acquisisci il lock:

```bash
python .agents/skills/agent-registry/scripts/lock_manager.py acquire \
  "src/auth.py" "kimi-1626451200"
```

L'`acquire` aggiorna **automaticamente** il registry nella stessa operazione
(D5): il path entra in `do_not_touch` e `space` della tua sessione. Non serve
(né si deve) aggiornare `do_not_touch` a mano. Il `release` lo rimuove da
`do_not_touch`. La sincronizzazione è best-effort: se la sessione non esiste
nel registry il lock funziona comunque (warning su stderr).

Se il file è occupato, ricevi un messaggio di blocco con l'owner: **non
modificare il file** e avvisa l'utente.

### 6. Fine sessione: `end` (distillazione wiki)

Quando hai finito, esegui un LoopVerifyContext finale e chiudi con `end`,
passando i campi narrativi già compilati (consigliato):

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py end \
  "kimi-1626451200" \
  --router "Refactoring modulo auth: estratto token manager OAuth" \
  --cosa "Estratto il token manager in src/oauth.py e semplificato auth.py" \
  --come "Analisi dipendenze, estrazione classe, test di regressione" \
  --risolto "auth.py ingestibile (>800 righe), duplicazione logica token" \
  --bug "scadenza token non gestita in refresh,header Authorization duplicato" \
  --skill-tool "agent-registry,handoff,Read,Grep,Bash"
```

`end` produce `wiki/<session_id>.md` (frontmatter strutturato + context
completo nel body), fa upsert nel DB `wiki.db`, marca la sessione `Finished`
e rilascia i lock residui. I campi narrativi non passati diventano
"non documentato" e vengono segnalati nel campo `issues` della sessione.

Flag di `end`: `--router`, `--cosa`, `--come`, `--risolto`, `--bug` (CSV),
`--skill-tool` (CSV).

Opzionale ma consigliato: genera il campo `router` via LLM (e migliora i campi
rimasti "non documentato") con l'ingestione:

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py wiki ingest \
  "kimi-1626451200"
# oppure, per tutte le entry in sospeso:
python .agents/skills/agent-registry/scripts/registry_manager.py wiki ingest-pending
```

Se l'LLM non è configurato/raggiungibile l'entry resta `pending_ingest`, intatta
e senza perdita di dati: riprova più tardi con `wiki ingest-pending`.

### 7. `finish` e rilascio lock (chiusura senza distillazione)

`end` è la chiusura consigliata. Se devi solo chiudere lo stato senza
distillare:

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py finish \
  "kimi-1626451200"
python .agents/skills/agent-registry/scripts/lock_manager.py release \
  "src/auth.py" "kimi-1626451200"
```

`finish` marca `Finished` e svuota `do_not_touch`; rilascia comunque
esplicitamente ogni lock acquisito.

---

## Lock: heartbeat, batch check, pre-flight

### Heartbeat

I lock hanno un timeout (default 120 secondi). Per tenerli vivi, lancia un
heartbeat in background:

```bash
python .agents/skills/agent-registry/scripts/lock_manager.py heartbeat-loop \
  "src/auth.py" "kimi-1626451200" 30 &
```

Il numero finale è l'intervallo in secondi (opzionale, default 30). Esiste
anche `heartbeat <path> <session_id>` per un singolo rinnovo.

### Batch check (pre-flight)

Prima di iniziare un task che tocca N file, controlla tutto in una chiamata:

```bash
python .agents/skills/agent-registry/scripts/lock_manager.py check \
  "src/auth.py" "src/oauth.py" "src/dashboard.py" --session-id "kimi-1626451200"
```

Output per path: `free`, `locked (owner, age)`, `locked-by-me`, `stale (owner)`.
Con un solo path il comando mantiene la forma storica (dict con warning).

---

## Sessioni zombie: cleanup e kill

### Cleanup

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py cleanup
```

Marca `Stop` le sessioni `OnWorking` zombie (PID morto o lock tutti stale) e
rilascia i lock residui. Una sessione con almeno un lock fresco non viene mai
toccata (potrebbe essere viva su un'altra macchina).

### Kill

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py kill \
  "claude-1720000000"           # aggiungi --force per saltare la verifica cmdline
```

Termina la sessione: se il PID registrato è attivo e la cmdline attuale è
compatibile con quella registrata (verifica anti-riuso PID, D6), manda
SIGTERM e dopo 5s SIGKILL; altrimenti esegue uno stop logico (tipico caso
cross-macchina o PID riusato). In ogni caso la sessione è marcata `Killed`
e i lock vengono rilasciati. Con `--force` la verifica cmdline è saltata.

---

## Aggiornare lo stato durante la sessione

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py update \
  "kimi-1626451200" --working-on "Implementazione lock filesystem" \
  --todo-past "setup,analisi" --todo-present "lock manager" \
  --todo-future "webapp" --space "scripts/lock_manager.py" --issues ""
```

Tutti i flag sono opzionali (ma almeno uno obbligatorio): `--working-on`,
`--todo-past/--todo-present/--todo-future`, `--space`, `--do-not-touch`,
`--issues`, `--status`. Le liste sono CSV. Nota: `do_not_touch` è gestito
automaticamente dai lock — non aggiornarlo a mano salvo casi eccezionali.

### Handoff

Quando salvi un handoff con la skill `handoff`, registra il path:

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py handoff \
  "kimi-1626451200" ".handoff-kimi/HANDOFF-007.md"
```

La convenzione è 1:1 — ogni sessione agente salva un handoff e ne registra il path.

### Altri comandi

- `show` — tutte le sessioni come dict Python (una per riga), per ispezione grezza.
- `wiki rebuild` — ricostruisce `wiki.db` dai file `wiki/*.md` (dopo un clone
  su una nuova macchina o se il DB è corrotto).

---

## Riferimento comandi completo

### `registry_manager.py`

| Comando | Sintassi | Scopo |
|---|---|---|
| `register` | `register <session_id> <provider> <ai_version> <working_on> [--space CSV] [--todo-present CSV]` | registra la sessione (cattura pid, cmdline, project, git_branch) |
| `update` | `update <session_id> [--working-on S] [--todo-past CSV] [--todo-present CSV] [--todo-future CSV] [--space CSV] [--do-not-touch CSV] [--issues S] [--status S]` | aggiorna i campi di una sessione |
| `finish` | `finish <session_id>` | marca `Finished` e svuota `do_not_touch` |
| `handoff` | `handoff <session_id> <handoff_path>` | registra il riferimento a un handoff |
| `show` | `show` | sessioni come dict (una per riga) |
| `status` | `status [--status S] [--provider P] [--project P]` | tabella human-readable + stato git-sync |
| `cleanup` | `cleanup` | marca `Stop` le sessioni zombie e rilascia i lock |
| `kill` | `kill <session_id> [--force]` | kill reale via PID o stop logico; marca `Killed`, rilascia lock |
| `context log` | `context log <session_id> "<entry>"` | appende una entry al context file |
| `end` | `end <session_id> [--router S] [--cosa S] [--come S] [--risolto S] [--bug CSV] [--skill-tool CSV]` | distilla il context in wiki entry e chiude la sessione |
| `wiki query` | `wiki query "<domanda>"` | router: il lavoro è già stato svolto? |
| `wiki show` | `wiki show <id\|session_id>` | entry wiki completa (JSON) |
| `wiki ingest` | `wiki ingest <session_id>` | ingestione LLM: genera il campo `router` |
| `wiki ingest-pending` | `wiki ingest-pending` | ingestisce tutte le entry `pending_ingest` |
| `wiki rebuild` | `wiki rebuild` | ricostruisce il DB dai `wiki/*.md` |

### `lock_manager.py`

| Comando | Sintassi | Scopo |
|---|---|---|
| `acquire` | `acquire <path> <session_id>` | acquisisce il lock (+ sync `do_not_touch`/`space`) |
| `release` | `release <path> <session_id>` | rilascia il lock (solo l'owner) |
| `check` | `check <path1> [path2 ...] [--session-id S]` | stato batch: free / locked / locked-by-me / stale |
| `heartbeat` | `heartbeat <path> <session_id>` | rinnova il timestamp del lock |
| `heartbeat-loop` | `heartbeat-loop <path> <session_id> [interval=30]` | loop di heartbeat (da lanciare in background) |

### `sync_manager.py`

| Comando | Sintassi | Scopo |
|---|---|---|
| `init` | `init --git-remote <url>` | inizializza la home come repo git con remote privato |
| `sync` | `sync` | forza un sync sincrono (add/commit/pull --rebase/push) |
| `status` | `status` | stato del sync (JSON) |
| `fetch-remote` | `fetch-remote <owner/repo> [--branch main]` | legge `registry.md` remoto via GitHub API (read-only) |

---

## Dashboard (web-app)

```bash
cd .agents/skills/agent-registry/scripts/webapp
pip install -r requirements.txt
uvicorn main:app --reload --port 8765
```

Poi apri http://localhost:8765. La dashboard si aggiorna da sola (SSE, ogni 5s)
e offre:

- **vista default**: sessioni di oggi (Roma) con status `OnWorking`;
- **filtri** per data, provider e status (`OnWorking`, `Stop`, `Finished`,
  `Killed`), più storico completo;
- **azioni**: kill di una sessione (con conferma), force-release dei lock
  (immediato se stale; se il lock è fresco chiede conferma esplicita),
  cleanup delle sessioni zombie;
- **vista wiki**: ricerca full-text sulle sessioni storicizzate con dettaglio
  espandibile (come_fatto, file_toccati, bug_trovati, context).

API REST principali (usabili anche da CLI/curl): `GET /api/sessions`
(filtri `date`/`provider`/`status`/`all`), `GET /api/locks`, `GET /api/sync`,
`GET /api/wiki?q=`, `GET /api/wiki/{id}`, `POST /api/sessions/{id}/kill`,
`POST /api/locks/force-release`, `POST /api/cleanup`.

---

## Git-sync (multi-macchina)

La home `~/.agent-registry/` può essere un repository git con remote privato:
dopo ogni scrittura viene schedulato automaticamente un sync in background
(`add` → `commit` → `pull --rebase` → `push`, con debounce ~2s in thread
daemon). Setup una tantum:

```bash
python .agents/skills/agent-registry/scripts/sync_manager.py init \
  --git-remote git@github.com:<tuo-user>/<repo-privato-registry>.git
```

Crea la home se manca, `git init`, `.gitignore` (locks/, wiki.db, *.tmp,
sync-status.json, __pycache__/), identità git locale, primo commit e remote
`origin`.

Principi:

- **Best-effort totale**: nessuna operazione del registry fallisce per colpa
  del sync. Offline o push rifiutato? Il push è rimandato al sync successivo e
  lo stato è visibile in `status` (riga finale) e in `sync_manager.py status`.
- **Niente conflitti nel caso normale**: i dati vivono in file per-sessione
  (un owner per file); `registry.md` è una vista rigenerata e in caso di
  conflitto viene ricostruita dai `sessions/*.yaml`.
- **Dati locali mai sincronizzati**: `locks/`, `wiki.db`, `sync-status.json`
  sono gitignored. Dopo un clone su una nuova macchina, ricostruisci l'indice
  wiki con `wiki rebuild`.

Per leggere il registry da una macchina senza setup (read-only, via GitHub
Contents API):

```bash
GITHUB_TOKEN=... python .agents/skills/agent-registry/scripts/sync_manager.py \
  fetch-remote <owner>/<repo> --branch main
```

---

## Variabili d'ambiente

| Variabile | Scopo |
|---|---|
| `AGENT_REGISTRY_HOME` | Override della home del registry (default `~/.agent-registry/`) |
| `AGENT_REGISTRY_PATH` | **Deprecata**: punta al file `registry.md`; la home è la directory genitore. Usa `AGENT_REGISTRY_HOME` |
| `KIMI_API_KEY` / `MOONSHOT_API_KEY` | API key per ingestione wiki e router query (fallback l'una dell'altra) |
| `KIMI_BASE_URL` | Endpoint OpenAI-compatibile (default `https://api.moonshot.ai/v1`) |
| `KIMI_MODEL` | Modello (default `kimi-k2.5`) |
| `GITHUB_TOKEN` / `GH_TOKEN` | Token per `fetch-remote` (lettura registry via GitHub API) |

Senza API key Kimi tutto funziona comunque: l'ingestione resta
`pending_ingest` e le query wiki ricadono sui candidati della ricerca
testuale FTS5 senza ranking LLM (fallback D8).

---

## Struttura della skill

```
.agents/skills/agent-registry/
├── SKILL.md                          # questo file
├── references/
│   └── registry-schema.yaml          # schema sessione YAML + wiki entry + DB
├── templates/
│   └── registry-template.md          # vista vuota (fallback creazione manuale)
├── evals/
│   └── evals.json                    # eval della skill
├── tests/                            # suite pytest (121 test)
└── scripts/
    ├── requirements.txt              # PyYAML, langchain, langchain-openai
    ├── registry_manager.py           # sessioni, vista, cleanup, kill, context, end, wiki CLI
    ├── lock_manager.py               # lock filesystem + heartbeat + batch check
    ├── sync_manager.py               # git-sync multi-macchina + fetch read-only
    ├── wiki_manager.py               # wiki markdown (fonte) + SQLite FTS5 (indice)
    ├── wiki_ingest.py                # ingestione e router query via LangChain + Kimi
    └── webapp/                       # dashboard FastAPI (main.py + static/)
```

---

## Note tecniche

- **Storage per-sessione**: ogni sessione vive in `sessions/<session_id>.yaml`
  (fonte di verità, un solo owner → niente conflitti di merge tra macchine).
  `registry.md` è una vista (YAML frontmatter + tabella markdown) rigenerata
  interamente a ogni scrittura: non modificarla a mano.
- Tutte le scritture sono atomiche (file tmp + rename, con lock flock).
- I lock sui file usano `fcntl` (Unix) e sono rilasciati automaticamente dal
  kernel se il processo muore; il timeout/heartbeat (default 120s) gestisce il
  caso di crash senza rilascio esplicito.
- La wiki ha due rappresentazioni: i markdown `wiki/*.md` sono la fonte di
  verità sincronizzata via git; `wiki.db` è solo un indice locale (SQLite con
  FTS5 su `router`, `cosa_fatto`, `bug_trovati`; se FTS5 non è disponibile
  nella build di sqlite3 la ricerca ricade su LIKE, stesso output più lento).
- L'ingestione e il router usano LangChain + Kimi (JSON strict con retry), ma
  il sistema è pienamente funzionale senza LLM: nessun dato va mai perso, le
  entry restano `pending_ingest` e le query ricadono sui candidati FTS.
- Timestamp in timezone Roma (`Europe/Rome`).
