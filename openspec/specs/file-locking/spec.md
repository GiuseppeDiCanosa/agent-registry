---
targets:
  - scripts/lock_manager.py
---

# file-locking Specification

## Purpose
Dare mutua esclusione reale su file e aree fra agenti AI CLI che operano come processi
one-shot: comandi che acquisiscono il lock e terminano subito. Da questo vincolo discende
l'intera capability — la proprietà di un lock deve vivere quanto il lavoro, non quanto il
processo che l'ha presa, quindi lo stato risiede nel contenuto di un file e non in un lock
advisory legato al ciclo di vita del processo. La capability copre acquisizione, scadenza
per timeout, rinnovo e rilascio riservati all'owner, e l'identità del lock indipendente
dalla directory di lavoro.

I lock restano **advisory**: proteggono gli agenti che li consultano, non i `write()` di
chi li ignora.

## Requirements
### Requirement: Mutua esclusione fra processi one-shot
Il lock manager SHALL garantire che, dato un path, al più un `session_id` alla volta ne risulti owner, e questa garanzia MUST reggere quando gli agenti operano come processi distinti e di breve durata che terminano subito dopo aver acquisito il lock. L'esito dell'acquisizione MUST essere determinato da uno stato che sopravvive alla terminazione del processo acquirente: il manager MUST NOT dipendere da lock advisory legati al ciclo di vita del processo (`fcntl.flock`/`lockf`) né da stato in memoria del processo per decidere se un path è occupato.

**Verified by**: [@test] tests/test_lock_cross_process.py

#### Scenario: Un secondo agente non può acquisire un lock valido
- **WHEN** l'agente A acquisisce il lock su un path in un processo che termina, e l'agente B tenta di acquisire lo stesso path prima della scadenza del timeout
- **THEN** il tentativo di B fallisce restituendo `locked: False` e il `session_id` di A come owner corrente, e il lock su disco continua ad attribuire la proprietà ad A

#### Scenario: L'owner conserva il proprio lock
- **WHEN** l'agente A, in un nuovo processo, verifica il proprio lock dopo che l'agente B ha tentato e fallito l'acquisizione
- **THEN** A risulta ancora owner del path

#### Scenario: Acquisizioni concorrenti in massa eleggono un solo vincitore
- **WHEN** N processi distinti tentano simultaneamente di acquisire il lock sullo stesso path
- **THEN** esattamente uno riceve `locked: True` e gli altri N-1 ricevono `locked: False`, e l'owner registrato su disco è il vincitore

#### Scenario: Path distinti non interferiscono
- **WHEN** due agenti acquisiscono lock su path diversi
- **THEN** entrambe le acquisizioni hanno successo

### Requirement: Riacquisizione idempotente da parte dell'owner
Il lock manager SHALL consentire allo stesso `session_id` di riacquisire un lock che già detiene, restituendo esito positivo senza errore e rinnovando la scadenza, affinché un agente che riavvia il proprio processo non si autoescluda dal lavoro già iniziato.

**Verified by**: [@test] tests/test_lock_cross_process.py

#### Scenario: L'owner riacquisisce da un nuovo processo
- **WHEN** l'agente A acquisisce un lock, il processo termina, e A riacquisisce lo stesso path con lo stesso `session_id`
- **THEN** l'acquisizione ha successo e A resta owner

### Requirement: Scadenza dei lock abbandonati
Il lock manager SHALL considerare stale un lock il cui ultimo rinnovo è più vecchio del timeout configurato, e MUST permetterne l'acquisizione a un altro agente, in modo che il crash di un agente non blocchi un file per sempre. Un lock non ancora scaduto MUST NOT essere considerato acquisibile. La rimozione di un lock stale MUST essere atomica rispetto ad acquisizioni concorrenti: se più agenti osservano lo stesso lock stale nello stesso istante, al più uno MUST riuscire ad acquisirlo.

**Verified by**: [@test] tests/test_lock_cross_process.py

#### Scenario: Un lock scaduto viene rilevato
- **WHEN** un lock non viene rinnovato per un tempo superiore al timeout e un altro agente ne verifica lo stato
- **THEN** il path risulta non locked e viene segnalato il `stale_owner` precedente

#### Scenario: Un lock scaduto può essere acquisito
- **WHEN** un agente acquisisce un path il cui lock è stale
- **THEN** l'acquisizione ha successo, il nuovo owner è l'acquirente e viene riportato lo `stale_owner` sostituito

#### Scenario: Corsa su un lock stale
- **WHEN** N processi tentano simultaneamente di acquisire lo stesso lock stale
- **THEN** esattamente uno riceve `locked: True`

### Requirement: Rinnovo riservato all'owner
Il lock manager SHALL permettere il rinnovo della scadenza (heartbeat) solo al `session_id` che detiene il lock, e MUST rifiutare il rinnovo richiesto da chiunque altro riportando l'owner corrente. Un rinnovo riuscito MUST posticipare la scadenza del lock; un rinnovo su un lock inesistente MUST fallire senza crearlo.

**Verified by**: [@test] tests/test_lock_cross_process.py

#### Scenario: L'owner rinnova
- **WHEN** l'owner esegue l'heartbeat sul proprio lock da un processo separato
- **THEN** il rinnovo ha successo e l'età del lock riparte da zero

#### Scenario: Un non-owner tenta il rinnovo
- **WHEN** un agente diverso dall'owner esegue l'heartbeat
- **THEN** l'operazione fallisce con errore `not owner` e riporta l'owner corrente

#### Scenario: Heartbeat su lock assente
- **WHEN** viene richiesto l'heartbeat su un path senza lock
- **THEN** l'operazione fallisce e nessun lock viene creato

#### Scenario: Il rinnovo previene la scadenza
- **WHEN** un lock viene rinnovato a intervalli inferiori al timeout
- **THEN** il lock non diventa mai stale e nessun altro agente può acquisirlo

### Requirement: Rilascio riservato all'owner
Il lock manager SHALL permettere il rilascio di un lock solo al `session_id` che lo detiene, e MUST rifiutare il rilascio richiesto da un altro agente lasciando il lock intatto. Dopo un rilascio riuscito il path MUST risultare libero e acquisibile da chiunque.

**Verified by**: [@test] tests/test_lock_cross_process.py

#### Scenario: L'owner rilascia
- **WHEN** l'owner rilascia il proprio lock da un processo separato
- **THEN** il rilascio ha successo e il path diventa acquisibile da un altro agente

#### Scenario: Un non-owner tenta il rilascio
- **WHEN** un agente diverso dall'owner tenta il rilascio
- **THEN** l'operazione fallisce con errore `not owner`, riporta l'owner corrente e il lock resta valido

#### Scenario: Rilascio di un lock inesistente
- **WHEN** viene rilasciato un path che non ha lock
- **THEN** l'operazione riporta successo senza sollevare eccezioni

### Requirement: Identità del lock indipendente dalla directory di lavoro
Il lock manager SHALL identificare il lock di un path a partire dal suo path assoluto risolto, in modo che riferimenti diversi allo stesso file (path relativo da directory di lavoro diverse, path assoluto) contendano lo stesso lock, e che file omonimi in progetti diversi non si blocchino a vicenda.

**Verified by**: [@test] tests/test_lock_cross_process.py

#### Scenario: Path relativo e assoluto sono lo stesso lock
- **WHEN** un agente acquisisce un lock usando un path relativo e un altro tenta di acquisire lo stesso file per path assoluto
- **THEN** il secondo tentativo viene bloccato

#### Scenario: File omonimi in progetti diversi
- **WHEN** due agenti acquisiscono `src/auth.py` in due directory di progetto distinte
- **THEN** entrambe le acquisizioni hanno successo

### Requirement: Directory dei lock configurabile
Il lock manager SHALL leggere la directory dei lock dalla variabile d'ambiente `AGENT_REGISTRY_LOCK_DIR` quando definita, ricadendo altrimenti sul default, e MUST crearla se assente. La configurazione MUST essere risolta a ogni operazione e non memorizzata all'import, così che i test e gli agenti possano isolare i lock senza modificare lo stato globale del modulo.

**Verified by**: [@test] tests/test_lock_cross_process.py

#### Scenario: Override via ambiente
- **WHEN** `AGENT_REGISTRY_LOCK_DIR` punta a una directory e un agente acquisisce un lock
- **THEN** il file di lock viene creato in quella directory e non nel default

#### Scenario: Directory assente
- **WHEN** la directory dei lock configurata non esiste al momento dell'acquisizione
- **THEN** viene creata automaticamente e l'acquisizione ha successo

### Requirement: Interfaccia a riga di comando con exit code significativi
Il lock manager SHALL esporre i comandi `acquire`, `release`, `check` e `heartbeat` via CLI, e MUST terminare con exit code 0 quando l'operazione riesce e diverso da 0 quando fallisce o viene bloccata, affinché un agente possa reagire all'esito senza dover interpretare il testo stampato.

**Verified by**: [@test] tests/test_lock_cli.py

#### Scenario: Acquisizione riuscita da CLI
- **WHEN** `lock_manager.py acquire <path> <sid>` acquisisce un path libero
- **THEN** il comando esce con codice 0

#### Scenario: Acquisizione bloccata da CLI
- **WHEN** `lock_manager.py acquire <path> <sid>` tenta un path già locked da un altro agente
- **THEN** il comando esce con codice diverso da 0 e nomina l'owner corrente

#### Scenario: Argomenti mancanti
- **WHEN** un comando viene invocato senza gli argomenti richiesti
- **THEN** il comando esce con codice diverso da 0 e stampa l'uso corretto, senza traceback

