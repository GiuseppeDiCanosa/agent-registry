## 1. Infrastruttura di test cross-process

- [x] 1.1 Creare `tests/conftest.py` con una fixture che isola `AGENT_REGISTRY_LOCK_DIR` e `AGENT_REGISTRY_PATH` in `tmp_path` per ogni test
- [x] 1.2 Aggiungere a `conftest.py` un helper che esegue un comando del manager in un **processo separato che termina**, restituendo exit code e stdout
- [x] 1.3 Aggiungere un helper che spara N processi simultanei sullo stesso path e raccoglie i loro esiti, per i test di corsa

## 2. Test rossi: la mutua esclusione (file-locking)

- [x] 2.1 `tests/test_lock_cross_process.py`: un secondo agente non può acquisire un lock valido preso da un processo terminato — riproduce il furto del lock
- [x] 2.2 L'owner conserva il proprio lock dopo il tentativo fallito di un altro agente
- [x] 2.3 N processi simultanei sullo stesso path: esattamente un vincitore
- [x] 2.4 Path distinti non interferiscono; l'owner riacquisisce in modo idempotente da un nuovo processo
- [x] 2.5 Staleness: rilevamento, acquisizione di un lock scaduto con `stale_owner`, e corsa di N processi su uno stale con un solo vincitore
- [x] 2.6 Heartbeat: rinnovo owner-only, rifiuto al non-owner, fallimento su lock assente, rinnovo che previene la scadenza
- [x] 2.7 Release: rilascio owner-only, rifiuto al non-owner con lock intatto, rilascio di lock inesistente senza eccezioni
- [x] 2.8 Identità del lock: path relativo e assoluto contendono lo stesso lock; file omonimi in progetti diversi no
- [x] 2.9 Configurazione: `AGENT_REGISTRY_LOCK_DIR` rispettato e directory creata se assente
- [x] 2.10 Verificare che 2.1–2.9 **falliscano** sul codice 0.1.0 e annotare quali passano per il motivo sbagliato

## 3. Test rossi: il registry (agent-registry)

- [x] 3.1 `tests/test_registry_concurrency.py`: N processi registrano N sessioni simultaneamente, tutte devono sopravvivere — riproduce la perdita di registrazioni
- [x] 3.2 N processi aggiornano simultaneamente sessioni diverse senza perdite
- [x] 3.3 Il registry resta parsabile durante le scritture concorrenti
- [x] 3.4 `finish` rilascia i lock della sessione e lascia intatti quelli altrui
- [x] 3.5 Estendere `tests/test_registry_manager.py`: aggiornamento parziale, sessione inesistente non creata implicitamente, escaping di `|` e a capo anche nelle liste, `AGENT_REGISTRY_PATH` rispettato
- [x] 3.6 Verificare che 3.1–3.5 falliscano sul codice 0.1.0

## 4. Riscrittura di `scripts/lock_manager.py`

- [x] 4.1 Aggiungere l'header `GENERATED FROM SPEC` e risolvere `LOCK_DIR` a ogni chiamata via `AGENT_REGISTRY_LOCK_DIR`
- [x] 4.2 Identità del lock su `os.path.realpath`, con il path reale scritto dentro il file di lock
- [x] 4.3 Stato del lock nel contenuto del file (sopravvive al processo) e `flock` a serializzare la sola sezione critica; rimuovere `_OPEN_LOCK_FDS`. Il lock file non viene mai cancellato: il rilascio ne azzera il contenuto
      *(revisione in corso d'opera: l'approccio `O_EXCL` + `os.link()` previsto in design.md è stato scartato — vedi 4.4)*
- [x] 4.4 Takeover di lock stale dentro la sezione critica, dove due taker sono serializzati dal flock
      *(revisione: la verifica di `st_ino`/`st_mtime_ns` prima e dopo NON chiude la finestra, perché `unlink` agisce sul nome e non sull'inode — non esiste "cancella solo se è ancora quello che ho letto". design.md aggiornato di conseguenza)*
- [x] 4.5 Riacquisizione idempotente dell'owner, con rinnovo della scadenza
- [x] 4.6 `heartbeat` e `release_lock` owner-only, con la verifica dell'owner e l'azione nella stessa sezione critica
- [x] 4.7 `is_locked` non cancella più i lock stale: si limita a riportarli
- [x] 4.8 CLI con exit code significativi e uso stampato senza traceback sugli argomenti mancanti

## 5. Riscrittura di `scripts/registry_manager.py`

- [x] 5.1 Aggiungere l'header `GENERATED FROM SPEC` e risolvere il path a ogni chiamata
- [x] 5.2 Introdurre `registry.lock` dedicato e mai rinominato, con un context manager che tiene `flock` per l'intera sezione critica
- [x] 5.3 Portare lettura e scrittura dentro la stessa sezione critica: `register_session`, `update_session`, `unregister_session`, `add_handoff_ref`
- [x] 5.4 Scrittura atomica via `tempfile` + `os.replace`, con `fsync` prima del replace
- [x] 5.5 `update_session` segnala l'assenza della sessione invece di crearla o tacere
- [x] 5.6 `unregister_session` rilascia i lock della sessione, saltando quelli di cui non è owner
- [x] 5.7 Escaping di `|` e a capo in ogni cella, incluse quelle derivate da liste
- [x] 5.8 CLI con exit code significativi
- [x] 5.9 Blocco di protocollo rigenerato a ogni scrittura fra frontmatter e tabella, con l'avvertenza sui lock advisory
- [x] 5.10 `tests/test_registry_protocol.py`: presenza nel registry nuovo, sopravvivenza agli aggiornamenti, parse non alterato, ripristino dopo manomissione
- [x] 5.11 Allineare `templates/registry-template.md` al blocco canonico, con un test che impedisca la divergenza fra template e codice

## 6. Verde e allineamento

- [x] 6.1 Eseguire l'intera suite: tutti i test dei gruppi 2 e 3 devono passare
- [x] 6.2 Rimuovere i test in-process della 0.1.0 resi obsoleti, documentando nel commit perché erano ciechi
- [x] 6.3 Verificare che `scripts/webapp/main.py` funzioni ancora dopo il cambio di serializzazione
- [x] 6.4 Aggiornare `SKILL.md`: path indipendenti dall'installazione, natura advisory dei lock, avvertenza su filesystem sincronizzati/di rete, rimozione del riferimento a `references/registry-schema.yaml` inesistente
- [x] 6.5 Eseguire `bash scripts/verify.sh`: link `[@test]`, ownership dei target e suite tutti verdi
- [x] 6.6 work-review requisito per requisito con evidenza `file:riga`
