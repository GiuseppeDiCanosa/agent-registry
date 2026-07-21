# Delta spec: agent-registry-storage

## ADDED Requirements

### Requirement: Pre-validazione del remote prima del setup
Il sistema SHALL validare il remote git prima di qualsiasi side-effect sul filesystem locale, eseguendo un accesso in sola lettura (equivalente a `git ls-remote`) e classificando l'esito in uno di: URL malformato, autenticazione fallita, remote non raggiungibile, remote vuoto, remote popolato. In caso di esito negativo il sistema MUST NOT modificare la home del registry.

#### Scenario: URL malformato
- **WHEN** l'utente fornisce un URL remote sintatticamente invalido
- **THEN** il sistema SHALL rifiutare il setup con un errore esplicito e SHALL lasciare la home invariata

#### Scenario: Autenticazione fallita
- **WHEN** l'accesso al remote fallisce per credenziali mancanti o insufficienti (chiave SSH assente, token HTTPS non configurato)
- **THEN** il sistema SHALL rifiutare il setup riportando che l'autenticazione è fallita e indicando quale meccanismo configurare

#### Scenario: Remote non raggiungibile
- **WHEN** la rete non è disponibile o l'host non risponde durante la validazione
- **THEN** il sistema SHALL segnalare il remote come non raggiungibile, distinguendolo da un fallimento di autenticazione

### Requirement: Setup guidato a tre rami
Il sistema SHALL scegliere la strategia di setup in base allo stato combinato di home locale e remote: (a) remote vuoto → inizializzazione della home come repo git, primo commit e push; (b) remote popolato e home senza repository git → clone del remote nella home; (c) remote popolato e home con repository git e dati locali → configurazione del remote, pull con rebase e risoluzione dei conflitti rigenerando la vista dai file per-sessione. Il comando CLI di init esistente SHALL instradare attraverso la stessa logica di validazione e scelta del ramo.

#### Scenario: Prima macchina con remote vuoto
- **WHEN** il remote è vuoto e la home non è un repository git
- **THEN** il sistema SHALL inizializzare la home, creare il primo commit ed eseguire il push iniziale

#### Scenario: Seconda macchina
- **WHEN** il remote contiene dati e la home non è un repository git
- **THEN** il sistema SHALL clonare il remote nella home, rendendo disponibili localmente sessioni, wiki e context delle altre macchine

#### Scenario: Home con dati locali e remote popolato
- **WHEN** la home è un repository git con dati locali e il remote contiene dati
- **THEN** il sistema SHALL integrare i dati con pull e rebase, e in caso di conflitto SHALL risolvere rigenerando la vista dai file per-sessione senza perdere sessioni locali

#### Scenario: Nessuna perdita di dati locali
- **WHEN** il ramo di integrazione (c) incontra un conflitto su un file per-sessione
- **THEN** il sistema SHALL preservare i file per-sessione locali e remoti e rigenerare le viste derivate, invece di sovrascrivere dati di sessione

### Requirement: Warning su repository pubblico
Quando il remote è un repository GitHub verificabile come pubblico, il sistema SHALL avvisare che il registry contiene contesto di lavoro potenzialmente sensibile e SHALL richiedere una conferma esplicita prima di procedere con il setup.

#### Scenario: Repo pubblico rifiutato
- **WHEN** il remote è pubblico e l'utente non conferma
- **THEN** il sistema MUST NOT procedere con il setup

#### Scenario: Repo pubblico confermato
- **WHEN** il remote è pubblico e l'utente conferma esplicitamente
- **THEN** il sistema SHALL procedere con il setup registrando la conferma nell'esito

### Requirement: Identità git per macchina
I commit automatici di sync SHALL usare un'identità git che include l'hostname della macchina, in modo che la history del repository condiviso distingua la provenienza dei commit.

#### Scenario: Commit riconducibili alla macchina
- **WHEN** il sistema esegue un commit di auto-sync su due macchine diverse
- **THEN** i commit SHALL avere identità distinguibili per macchina
