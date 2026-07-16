## 1. Infrastruttura di test cross-process

- [ ] 1.1 Creare `tests/conftest.py` con una fixture che isola `AGENT_REGISTRY_LOCK_DIR` e `AGENT_REGISTRY_PATH` in `tmp_path` per ogni test
- [ ] 1.2 Aggiungere a `conftest.py` un helper che esegue un comando del manager in un **processo separato che termina**, restituendo exit code e stdout
- [ ] 1.3 Aggiungere un helper che spara N processi simultanei sullo stesso path e raccoglie i loro esiti, per i test di corsa

## 2. Test rossi: la mutua esclusione (file-locking)

- [ ] 2.1 `tests/test_lock_cross_process.py`: un secondo agente non può acquisire un lock valido preso da un processo terminato — riproduce il furto del lock
- [ ] 2.2 L'owner conserva il proprio lock dopo il tentativo fallito di un altro agente
- [ ] 2.3 N processi simultanei sullo stesso path: esattamente un vincitore
- [ ] 2.4 Path distinti non interferiscono; l'owner riacquisisce in modo idempotente da un nuovo processo
- [ ] 2.5 Staleness: rilevamento, acquisizione di un lock scaduto con `stale_owner`, e corsa di N processi su uno stale con un solo vincitore
- [ ] 2.6 Heartbeat: rinnovo owner-only, rifiuto al non-owner, fallimento su lock assente, rinnovo che previene la scadenza
- [ ] 2.7 Release: rilascio owner-only, rifiuto al non-owner con lock intatto, rilascio di lock inesistente senza eccezioni
- [ ] 2.8 Identità del lock: path relativo e assoluto contendono lo stesso lock; file omonimi in progetti diversi no
- [ ] 2.9 Configurazione: `AGENT_REGISTRY_LOCK_DIR` rispettato e directory creata se assente
- [ ] 2.10 Verificare che 2.1–2.9 **falliscano** sul codice 0.1.0 e annotare quali passano per il motivo sbagliato

## 3. Test rossi: il registry (agent-registry)

- [ ] 3.1 `tests/test_registry_concurrency.py`: N processi registrano N sessioni simultaneamente, tutte devono sopravvivere — riproduce la perdita di registrazioni
- [ ] 3.2 N processi aggiornano simultaneamente sessioni diverse senza perdite
- [ ] 3.3 Il registry resta parsabile durante le scritture concorrenti
- [ ] 3.4 `finish` rilascia i lock della sessione e lascia intatti quelli altrui
- [ ] 3.5 Estendere `tests/test_registry_manager.py`: aggiornamento parziale, sessione inesistente non creata implicitamente, escaping di `|` e a capo anche nelle liste, `AGENT_REGISTRY_PATH` rispettato
- [ ] 3.6 Verificare che 3.1–3.5 falliscano sul codice 0.1.0

## 4. Riscrittura di `scripts/lock_manager.py`

- [ ] 4.1 Aggiungere l'header `GENERATED FROM SPEC` e risolvere `LOCK_DIR` a ogni chiamata via `AGENT_REGISTRY_LOCK_DIR`
- [ ] 4.2 Identità del lock su `os.path.realpath`, con il path reale scritto dentro il file di lock
- [ ] 4.3 Acquisizione via file temporaneo + `os.link()` atomico; rimuovere `_OPEN_LOCK_FDS` e ogni uso di `flock`
- [ ] 4.4 Takeover di lock stale verificando l'identità del file (`st_ino`, `st_mtime_ns`) prima e dopo, per non far vincere due agenti
- [ ] 4.5 Riacquisizione idempotente dell'owner, con rinnovo della scadenza
- [ ] 4.6 `heartbeat` e `release_lock` owner-only, con la verifica dell'owner e l'azione nella stessa sezione critica
- [ ] 4.7 `is_locked` non cancella più i lock stale: si limita a riportarli
- [ ] 4.8 CLI con exit code significativi e uso stampato senza traceback sugli argomenti mancanti

## 5. Riscrittura di `scripts/registry_manager.py`

- [ ] 5.1 Aggiungere l'header `GENERATED FROM SPEC` e risolvere il path a ogni chiamata
- [ ] 5.2 Introdurre `registry.lock` dedicato e mai rinominato, con un context manager che tiene `flock` per l'intera sezione critica
- [ ] 5.3 Portare lettura e scrittura dentro la stessa sezione critica: `register_session`, `update_session`, `unregister_session`, `add_handoff_ref`
- [ ] 5.4 Scrittura atomica via `tempfile` + `os.replace`, con `fsync` prima del replace
- [ ] 5.5 `update_session` segnala l'assenza della sessione invece di crearla o tacere
- [ ] 5.6 `unregister_session` rilascia i lock della sessione, saltando quelli di cui non è owner
- [ ] 5.7 Escaping di `|` e a capo in ogni cella, incluse quelle derivate da liste
- [ ] 5.8 CLI con exit code significativi

## 6. Verde e allineamento

- [ ] 6.1 Eseguire l'intera suite: tutti i test dei gruppi 2 e 3 devono passare
- [ ] 6.2 Rimuovere i test in-process della 0.1.0 resi obsoleti, documentando nel commit perché erano ciechi
- [ ] 6.3 Verificare che `scripts/webapp/main.py` funzioni ancora dopo il cambio di serializzazione
- [ ] 6.4 Aggiornare `SKILL.md`: path indipendenti dall'installazione, natura advisory dei lock, avvertenza su filesystem sincronizzati/di rete, rimozione del riferimento a `references/registry-schema.yaml` inesistente
- [ ] 6.5 Eseguire `bash scripts/verify.sh`: link `[@test]`, ownership dei target e suite tutti verdi
- [ ] 6.6 work-review requisito per requisito con evidenza `file:riga`
