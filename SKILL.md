---
name: agent-registry
description: >
  Coordina multipli agenti AI CLI (Kimi, Claude, Gemini, OpenAI, ecc.) che lavorano
  contemporaneamente sullo stesso progetto. Usa questa skill SEMPRE quando lavori in
  parallelo con altre AI CLI, quando devi salvare lo stato di una sessione condivisa,
  quando vuoi evitare sovrascritture su file toccati da altri agenti, o quando l'utente
  parla di "multi-tap", "registry agenti", "coordination", "lock file", "handoff condiviso",
  "chi sta lavorando su cosa" o "evitare che le AI si pestino i piedi".
---

# Agent Registry — Coordination Multi-Agent

Questa skill permette a più agenti AI CLI di lavorare sullo stesso progetto senza
sovrascriversi a vicenda. Mantiene un **registry condiviso** sul Desktop che dice:

- chi sta lavorando (provider, versione, sessione)
- su cosa sta lavorando (`working on`, todo passato/presente/futuro)
- quali file sta toccando (`space`)
- quali file sono bloccati (`do-not-touch` locks)
- quali problemi ha riscontrato (`issues`)
- quali handoff ha salvato (`handoff`)

Il registry è un file markdown leggibile sia da umani che da macchine:

```
~/Desktop/agent-registry/registry.md
```

C'è anche una **web-app locale** in Python + FastAPI per monitorare lo stato in tempo reale.

---

## Regola d'oro

> **Leggi il registry prima di toccare qualsiasi file. Non modificare mai un file che è nel campo `do_not_touch` di un altro agente.**

---

## Primo passo all'avvio (obbligatorio)

Non appena viene caricata questa skill, l'agente DEVE:

1. Leggere `~/Desktop/agent-registry/registry.md`.
2. Identificare quali file/aree sono `do_not_touch` per altri agenti con status `OnWorking`.
3. Registrare la propria sessione con `scripts/registry_manager.py`.

Esempio:

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py register \
  "kimi-$(date +%s)" "Kimi" "2.7" "Refactoring modulo auth" \
  "src/auth.py,src/oauth.py" "analisi codice,scrittura test"
```

Argomenti:

1. `session_id` — identificativo univoco (es. `kimi-1626451200`)
2. `provider` — nome del provider CLI (Kimi, Claude, Gemini, OpenAI, ...)
3. `ai_version` — versione/modello (es. `Kimi 2.7`, `Claude Opus 4.8`)
4. `working_on` — descrizione sintetica del lavoro
5. `space` — file/aree toccate, separati da virgola
6. `todo_present` — task in corso, separati da virgola

---

## Aggiornare lo stato durante la sessione

Mentre lavori, aggiorna periodicamente `working_on`, `todo`, `space`, `issues`:

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py update \
  "kimi-1626451200" "Implementazione lock filesystem"
```

Per aggiornamenti più granulari usa Python importando `registry_manager`:

```python
from scripts.registry_manager import update_session
update_session(
    "kimi-1626451200",
    working_on="Implementazione lock filesystem",
    todo={"past": ["setup"], "present": ["lock manager"], "future": ["webapp"]},
    space=["scripts/lock_manager.py"],
    issues="",
)
```

---

## Lock su file/aree

Prima di modificare un file, acquisisci il lock con `lock_manager.py`:

```bash
python .agents/skills/agent-registry/scripts/lock_manager.py acquire \
  "src/auth.py" "kimi-1626451200"
```

Se il file è libero, il lock viene acquisito. Se è occupato, ricevi un messaggio di blocco.

Per aggiornare il registry con i file bloccati:

```python
from scripts.registry_manager import update_session
update_session(
    "kimi-1626451200",
    do_not_touch=["src/auth.py"],
)
```

### Heartbeat

I lock hanno un timeout (default 120 secondi). Per tenerli vivi, lancia un heartbeat in background:

```bash
python .agents/skills/agent-registry/scripts/lock_manager.py heartbeat-loop \
  "src/auth.py" "kimi-1626451200" 30 &
```

Il numero finale è l'intervallo in secondi (opzionale, default 30).

### Rilasciare un lock

```bash
python .agents/skills/agent-registry/scripts/lock_manager.py release \
  "src/auth.py" "kimi-1626451200"
```

---

## Integrazione con handoff

Quando salvi un handoff con la skill `handoff`, registra il path nel registry:

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py handoff \
  "kimi-1626451200" ".handoff-kimi/HANDOFF-007.md"
```

Oppure, da Python:

```python
from scripts.registry_manager import add_handoff_ref
add_handoff_ref("kimi-1626452000", ".handoff-kimi/HANDOFF-007.md")
```

La convenzione è 1:1 — ogni sessione agente salva un handoff e ne registra il path.

---

## Fine sessione

Quando hai finito, marca la sessione come `Finished` e rilascia tutti i lock:

```bash
python .agents/skills/agent-registry/scripts/registry_manager.py finish \
  "kimi-1626451200"
```

Poi rilascia esplicitamente ogni lock che hai acquisito:

```bash
python .agents/skills/agent-registry/scripts/lock_manager.py release \
  "src/auth.py" "kimi-1626451200"
```

---

## Web-app di monitoraggio

Avvia la web-app per vedere lo stato degli agenti in tempo reale:

```bash
cd .agents/skills/agent-registry/scripts/webapp
pip install -r requirements.txt
uvicorn main:app --reload --port 8765
```

Poi apri nel browser: http://localhost:8765

La tabella si aggiorna automaticamente ogni 5 secondi.

---

## Struttura della skill

```
.agents/skills/agent-registry/
├── SKILL.md                          # questo file
├── references/
│   └── registry-schema.yaml          # schema del registry
├── templates/
│   └── registry-template.md          # template vuoto del registry
└── scripts/
    ├── registry_manager.py           # lettura/scrittura registry
    ├── lock_manager.py               # lock filesystem + heartbeat
    └── webapp/                       # web-app FastAPI
```

---

## Note tecniche

- Il registry usa YAML frontmatter per i dati e una tabella markdown come vista leggibile.
- La scrittura nel registry è atomica e protetta da lock globale sul file.
- I lock sui file usano `fcntl` (Unix) e sono rilasciati automaticamente dal kernel se il processo muore.
- Il timeout/heartbeat gestisce il caso in cui un agente crasha senza rilasciare il lock.
- Timestamp in timezone Roma (`Europe/Rome`).
