## Why

La skill `agent-registry` 0.1.0 esiste per impedire che più agenti AI CLI si sovrascrivano a vicenda, ma non lo fa: nel flusso d'uso documentato (processi one-shot lanciati da bash) i lock non escludono nessuno e il registry perde le registrazioni concorrenti. Entrambi i difetti sono riproducibili.

Prova 1 — furto del lock, con timeout a 120s:

```
Agente A acquisisce auth.py   → {'locked': True, 'owner': 'claude-111'}
Agente B acquisisce auth.py   → {'locked': True, 'owner': 'kimi-222'}   ← doveva essere bloccato
A ricontrolla il proprio lock → "è locked da kimi-222"                   ← A ha perso il lock senza saperlo
```

Prova 2 — 8 agenti che si registrano in parallelo: 8 successi riportati, 1 solo agente sopravvive nel registry.

Le due cause tecniche: `fcntl.flock` viene rilasciato dal kernel all'uscita del processo, quindi un lock preso da un comando CLI che termina non protegge nulla; e `register_session` esegue il read-modify-write *fuori* dal lock, mentre `save_agents` prende il flock su un fd il cui inode viene poi sostituito da `shutil.move`, rendendo il lock inefficace anche tra processi vivi.

È il caso peggiore per una skill di coordinamento: un lock che concede due owner sullo stesso file è **peggio di nessun lock**, perché gli agenti agiscono con la falsa sicurezza di essere protetti. La suite attuale è verde (15/15) perché tutti i test girano in un unico processo, dove la mutua esclusione è garantita da un dict in RAM (`_OPEN_LOCK_FDS`) e non dal filesystem.

## What Changes

- **BREAKING**: `acquire_lock` non sovrascrive più un lock valido di un altro owner. Chi prima otteneva `{'locked': True}` rubando il lock ora riceve `{'locked': False}` con l'owner corrente. È il comportamento che la doc già prometteva.
- Il meccanismo di lock passa da `fcntl.flock` (legato alla vita del processo) a una creazione atomica `os.open(O_CREAT|O_EXCL)`, il cui esito sopravvive all'uscita del processo — l'unico modello compatibile con comandi CLI one-shot.
- Il rinnovo (`heartbeat`) e il rilascio (`release_lock`) diventano owner-only e verificati atomicamente; la rimozione di un lock stale non è più soggetta a race tra due agenti che la osservano insieme.
- Il registry esegue l'intero ciclo read-modify-write dentro un unico lock tenuto su un file dedicato (`registry.lock`) che non viene mai rinominato, così la scrittura atomica via rename di `registry.md` non invalida più il lock.
- `finish` rilascia anche i lock filesystem della sessione, eliminando la divergenza fra il campo `do_not_touch` del registry e la cartella `locks/`.
- `LOCK_DIR` diventa configurabile via env (`AGENT_REGISTRY_LOCK_DIR`), in simmetria con `AGENT_REGISTRY_PATH`, per rendere i lock testabili in isolamento.
- La suite di test viene riscritta per esercitare il flusso reale: **processi separati che terminano davvero**, non worker tenuti vivi da `sleep`.

## Capabilities

### New Capabilities
- `file-locking`: mutua esclusione su file/aree fra processi agente indipendenti e one-shot, con scadenza per timeout, rinnovo e rilascio riservati all'owner.
- `agent-registry`: stato condiviso e osservabile degli agenti attivi (chi lavora su cosa, cosa è bloccato, quali handoff esistono), con aggiornamenti concorrenti che non si perdono.

### Modified Capabilities

Nessuna: `openspec/specs/` non contiene ancora capability. Il codice 0.1.0 importato come baseline non ha mai avuto spec — questo change le introduce per la prima volta.

## Impact

- `scripts/lock_manager.py` — riscritto attorno a O_EXCL. API pubblica invariata nei nomi (`acquire_lock`, `release_lock`, `heartbeat`, `is_locked`, `check_and_warn`, `guarded_acquire`), cambia la semantica nei casi di conflitto.
- `scripts/registry_manager.py` — read-modify-write serializzato; `unregister_session` rilascia i lock.
- `tests/` — i test in-process che passavano su codice rotto vengono sostituiti da test cross-process.
- `SKILL.md` — i comandi documentati puntano a `.agents/skills/agent-registry/scripts/`, path che non esiste in nessuna installazione via `npx tessl i` (che installa in `.tessl/plugins/<ns>/agent-registry/`). Vanno resi indipendenti dal path di installazione.
- `scripts/webapp/main.py` — legge il registry tramite `registry_manager`; nessuna modifica funzionale attesa, ma va riverificato dopo il cambio di serializzazione.
- Nessuna nuova dipendenza esterna: la soluzione usa solo `os`, `errno`, `tempfile` e `fcntl` della stdlib.
