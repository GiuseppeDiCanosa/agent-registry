# Tasks — Notifiche composte dagli agenti

## 1. Deposito lato registry

- [x] 1.1 `registry_manager.py`: parametri facoltativi su `register` e `finish` per il testo di notifica, scritti sotto `notify` nel file di sessione tramite il percorso di scrittura concorrente esistente
- [x] 1.2 `tests/test_registry_manager.py`: deposito su register, deposito su finish, e assenza della mappa `notify` quando i parametri non sono passati

## 2. Firma a runtime

- [x] 2.1 `notifier/watchdog.py`: la firma non è più nel template — `render_message` la appone leggendo il `provider` della sessione, su entrambi i percorsi
- [x] 2.2 `notifier/messages.default.json`: rimuovere ogni firma dalle stringhe del pool pubblico
- [x] 2.3 `tests/notifier/test_watchdog.py`: la firma è quella del provider; un provider mai visto firma senza modifiche al codice; nessuna stringa del pool di default contiene una firma

## 3. Precedenza del testo composto

- [x] 3.1 `notifier/watchdog.py`: se la sessione dichiara un testo per l'evento rilevato, inviarlo verbatim invece di pescare dal pool
- [x] 3.2 `tests/notifier/test_watchdog.py`: il testo composto ha la precedenza e il pool non viene usato per quell'invio

## 4. Consumo distruttivo

- [x] 4.1 `notifier/watchdog.py`: dopo l'invio rimuovere la chiave dal file di sessione, sotto lock `lock_manager`, ripristinando l'`mtime` precedente con `os.utime()`
- [x] 4.2 `notifier/watchdog.py`: un lock non disponibile non solleva e non interrompe il ciclo
- [x] 4.3 `tests/notifier/test_watchdog.py`: un solo invio per composizione; `mtime` invariato dopo il consumo; lock occupato non rompe il ciclo

## 5. Istruzione agli agenti

- [x] 5.1 `SKILL.md`: sezione che istruisce ogni agente a comporre il proprio messaggio con la propria voce al momento di registrare e di chiudere la sessione
- [x] 5.2 Ripulire le firme da `notifier/messages.local.json` sulla macchina locale (gitignored, non committabile)

## 6. Chiusura

- [x] 6.1 `bash scripts/verify.sh` verde
- [x] 6.2 `work-review` requisito-per-requisito con evidenza file:riga
- [x] 6.3 Osservazione end-to-end: una sessione Claude che chiude produce su WhatsApp il testo scritto da Claude, firmato Claude
