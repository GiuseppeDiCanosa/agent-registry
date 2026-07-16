---
targets:
  - scripts/registry_manager.py
---

## ADDED Requirements

### Requirement: Aggiornamenti concorrenti che non si perdono
Il registry manager SHALL serializzare l'intero ciclo di lettura-modifica-scrittura del registry, in modo che aggiornamenti provenienti da processi distinti e simultanei siano tutti preservati. Il lock che protegge il ciclo MUST essere tenuto su un file dedicato che non viene mai sostituito, così che la scrittura atomica del registry tramite rename non invalidi il lock stesso; il manager MUST NOT eseguire la lettura fuori dalla sezione critica che ne protegge la scrittura.

**Verified by**: [@test] tests/test_registry_concurrency.py

#### Scenario: Registrazioni simultanee
- **WHEN** N processi distinti registrano simultaneamente N sessioni con `session_id` diversi
- **THEN** il registry contiene tutte e N le sessioni

#### Scenario: Aggiornamenti simultanei su sessioni diverse
- **WHEN** N processi aggiornano simultaneamente N sessioni preesistenti distinte
- **THEN** ogni sessione riflette il proprio aggiornamento e nessuna viene persa

#### Scenario: Il registry resta sempre leggibile
- **WHEN** un processo legge il registry mentre altri lo stanno aggiornando
- **THEN** la lettura restituisce un documento valido e parsabile, mai un file troncato o parziale

### Requirement: Registrazione di una sessione agente
Il registry manager SHALL registrare una sessione con `session_id`, provider, versione del modello, descrizione del lavoro, aree toccate e todo correnti, assegnandole stato `OnWorking` e istante di avvio. Registrare un `session_id` già presente MUST sostituire la voce esistente invece di duplicarla.

**Verified by**: [@test] tests/test_registry_manager.py

#### Scenario: Nuova sessione
- **WHEN** un agente registra un `session_id` non presente
- **THEN** la sessione compare nel registry con stato `OnWorking` e i campi forniti

#### Scenario: Re-registrazione dello stesso id
- **WHEN** un agente registra un `session_id` già presente
- **THEN** il registry contiene una sola voce per quel `session_id`, con i dati aggiornati

### Requirement: Aggiornamento dei campi di sessione
Il registry manager SHALL permettere di aggiornare i campi di una sessione esistente lasciando invariati quelli non specificati, e MUST segnalare l'assenza della sessione quando l'id richiesto non esiste, senza crearla implicitamente.

**Verified by**: [@test] tests/test_registry_manager.py

#### Scenario: Aggiornamento parziale
- **WHEN** viene aggiornato solo `working_on` di una sessione
- **THEN** `working_on` cambia e gli altri campi restano invariati

#### Scenario: Sessione inesistente
- **WHEN** viene aggiornata una sessione con un id non registrato
- **THEN** l'operazione segnala l'assenza e il registry resta invariato

### Requirement: Chiusura di sessione con rilascio dei lock
Il registry manager SHALL, alla chiusura di una sessione, marcarla `Finished`, svuotarne il campo `do_not_touch` e rilasciare i lock filesystem che la sessione deteneva, affinché il registry e la directory dei lock non divergano. La chiusura MUST NOT rilasciare lock di cui la sessione non è owner.

**Verified by**: [@test] tests/test_registry_concurrency.py

#### Scenario: Chiusura rilascia i lock
- **WHEN** una sessione con lock attivi su file dichiarati in `do_not_touch` viene chiusa
- **THEN** la sessione risulta `Finished`, `do_not_touch` è vuoto e quei path tornano acquisibili da un altro agente

#### Scenario: I lock altrui restano intatti
- **WHEN** una sessione viene chiusa mentre un'altra sessione detiene lock su altri path
- **THEN** i lock dell'altra sessione restano validi

### Requirement: Percorso del registry configurabile
Il registry manager SHALL leggere il percorso del registry dalla variabile d'ambiente `AGENT_REGISTRY_PATH` quando definita, ricadendo altrimenti sul default, e MUST creare il file e le directory mancanti alla prima scrittura. Il percorso MUST essere risolto a ogni operazione e non memorizzato all'import.

**Verified by**: [@test] tests/test_registry_manager.py

#### Scenario: Override via ambiente
- **WHEN** `AGENT_REGISTRY_PATH` punta a un file inesistente e un agente registra una sessione
- **THEN** il file viene creato in quel percorso con la sessione registrata

### Requirement: Registry leggibile da umani e da macchine
Il registry manager SHALL mantenere il registry come documento markdown con frontmatter YAML quale dato autorevole e una tabella markdown come vista leggibile, rigenerata a ogni scrittura per restare coerente con il frontmatter. I valori che contengono caratteri capaci di rompere la tabella — barra verticale o a capo — MUST essere neutralizzati in ogni cella, inclusi i campi derivati da liste.

**Verified by**: [@test] tests/test_registry_manager.py

#### Scenario: Tabella coerente col frontmatter
- **WHEN** una sessione viene registrata o aggiornata
- **THEN** la tabella markdown contiene una riga per ogni agente presente nel frontmatter

#### Scenario: Valore con barra verticale
- **WHEN** un campo di sessione, scalare o dentro una lista, contiene il carattere `|` o un a capo
- **THEN** la tabella resta strutturalmente valida e il registry riletto restituisce il valore originale

### Requirement: Riferimento all'handoff di sessione
Il registry manager SHALL permettere di associare a una sessione il percorso dell'handoff salvato, rendendolo leggibile a chiunque consulti il registry.

**Verified by**: [@test] tests/test_registry_manager.py

#### Scenario: Registrazione di un handoff
- **WHEN** un agente registra il path di un handoff per la propria sessione
- **THEN** il campo handoff della sessione contiene quel path

### Requirement: Interfaccia a riga di comando con exit code significativi
Il registry manager SHALL esporre i comandi `register`, `update`, `finish`, `handoff` e `show` via CLI, e MUST terminare con exit code 0 quando l'operazione riesce e diverso da 0 quando fallisce, senza mai riportare successo per un'operazione non avvenuta.

**Verified by**: [@test] tests/test_registry_cli.py

#### Scenario: Registrazione da CLI
- **WHEN** `registry_manager.py register` registra una nuova sessione
- **THEN** il comando esce con codice 0 e la sessione è presente nel registry

#### Scenario: Aggiornamento di una sessione inesistente
- **WHEN** `registry_manager.py update` riceve un `session_id` non registrato
- **THEN** il comando esce con codice diverso da 0 e segnala l'assenza, senza dichiarare successo

#### Scenario: Argomenti mancanti
- **WHEN** un comando viene invocato senza gli argomenti richiesti
- **THEN** il comando esce con codice diverso da 0 e stampa l'uso corretto, senza traceback
