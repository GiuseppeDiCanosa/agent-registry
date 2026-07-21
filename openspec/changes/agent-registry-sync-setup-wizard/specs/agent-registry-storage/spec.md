# Delta spec: agent-registry-storage

## ADDED Requirements

### Requirement: Pre-validazione del remote prima del setup
Il sistema SHALL validare il remote git prima di qualsiasi side-effect sul filesystem locale, eseguendo un accesso in sola lettura al remote e classificando l'esito in uno di: URL malformato, autenticazione fallita, remote non raggiungibile, remote vuoto, remote popolato. In caso di esito negativo il sistema MUST NOT modificare la home del registry.

#### Scenario: URL malformato
- **WHEN** l'utente fornisce un URL remote che non rispetta le forme accettate (SSH scp-like o `scheme://host/path`)
- **THEN** il sistema SHALL rifiutare il setup con un errore esplicito e SHALL lasciare la home invariata

#### Scenario: Autenticazione fallita
- **WHEN** l'accesso al remote fallisce per credenziali mancanti o insufficienti (chiave SSH assente, token HTTPS non configurato)
- **THEN** il sistema SHALL rifiutare il setup riportando che l'autenticazione è fallita e indicando quale meccanismo configurare

#### Scenario: Remote non raggiungibile
- **WHEN** la rete non è disponibile o l'host non risponde durante la validazione
- **THEN** il sistema SHALL segnalare il remote come non raggiungibile, distinguendolo da un fallimento di autenticazione

### Requirement: Setup guidato a tre rami
Il sistema SHALL scegliere la strategia di setup in base allo stato combinato di home locale e remote: (a) "inizializzazione" — remote vuoto: la home diventa un repository git con primo commit e push iniziale; (b) "clone" — remote popolato e home senza repository git e senza dati locali: i dati del remote vengono clonati nella home; (c) "integrazione" — remote popolato e home con repository git o con dati locali: le history vengono integrate e i conflitti risolti rigenerando le viste derivate dai file per-sessione. Per "dati locali" si intende la presenza di sessioni, wiki entry o context con contenuto nella home. Clone e integrazione SHALL operare sul branch di default del remote. Il comando CLI di init esistente SHALL instradare attraverso la stessa logica di validazione e scelta del ramo.

#### Scenario: Prima macchina con remote vuoto
- **WHEN** il remote è vuoto e la home non è un repository git
- **THEN** il sistema SHALL inizializzare la home, creare il primo commit ed eseguire il push iniziale

#### Scenario: Seconda macchina
- **WHEN** il remote contiene dati e la home non è un repository git e non ha dati locali
- **THEN** il sistema SHALL clonare il remote nella home sul suo branch di default, rendendo disponibili localmente sessioni, wiki e context delle altre macchine

#### Scenario: Home con dati locali e remote popolato
- **WHEN** la home è un repository git con dati locali oppure contiene dati locali senza essere un repository, e il remote contiene dati
- **THEN** il sistema SHALL integrare le history e, in caso di conflitto, SHALL risolvere rigenerando le viste derivate dai file per-sessione senza perdere sessioni locali

#### Scenario: Nessuna perdita di dati locali
- **WHEN** il ramo di integrazione incontra un conflitto su un file per-sessione
- **THEN** il sistema SHALL preservare i file per-sessione locali e remoti e rigenerare le viste derivate, invece di sovrascrivere dati di sessione

#### Scenario: Integrazione solo con conferma
- **WHEN** il caso rilevato è l'integrazione (ramo c) e l'utente non ha confermato esplicitamente
- **THEN** il sistema SHALL richiedere una conferma esplicita e MUST NOT procedere senza, a prescindere dall'entry point (CLI o dashboard)

#### Scenario: Setup già configurato
- **WHEN** il setup viene invocato ma il git-sync è già configurato sulla home
- **THEN** il sistema SHALL segnalare che il sync è già attivo e MUST NOT modificare la configurazione esistente

#### Scenario: Remote popolato durante il setup
- **WHEN** la validazione ha rilevato un remote vuoto ma il push iniziale fallisce perché un'altra macchina ha popolato il remote nel frattempo
- **THEN** il sistema SHALL ricadere sul ramo di integrazione (con la relativa conferma) invece di fallire o forzare il push

### Requirement: Warning su repository pubblico
Quando il remote è un repository GitHub verificabile come pubblico, il sistema SHALL avvisare che il registry contiene contesto di lavoro potenzialmente sensibile e SHALL richiedere una conferma esplicita prima di procedere con il setup. Quando la visibilità non è verificabile (token assente o host non GitHub), il sistema SHALL informare che la visibilità non è stata verificata e proseguire.

#### Scenario: Repo pubblico rifiutato
- **WHEN** il remote è pubblico e l'utente non conferma
- **THEN** il sistema MUST NOT procedere con il setup

#### Scenario: Repo pubblico confermato
- **WHEN** il remote è pubblico e l'utente conferma esplicitamente
- **THEN** il sistema SHALL procedere con il setup registrando la conferma nell'esito

#### Scenario: Visibilità non verificabile
- **WHEN** la visibilità del remote non può essere verificata (token assente o host non GitHub)
- **THEN** il sistema SHALL includere nell'esito l'avviso "visibilità non verificata" e proseguire con il setup

### Requirement: Identità git per macchina
Nei repository inizializzati da questa versione in poi, i commit automatici di sync SHALL usare un'identità git che include l'hostname della macchina, in modo che la history del repository condiviso distingua la provenienza dei commit. I repository già inizializzati MUST NOT essere modificati.

#### Scenario: Commit riconducibili alla macchina
- **WHEN** il sistema esegue un commit di auto-sync su due macchine diverse con hostname diversi
- **THEN** i commit SHALL avere identità distinguibili per macchina

#### Scenario: Repository esistente non toccato
- **WHEN** la home era già un repository git inizializzato prima di questa versione
- **THEN** il sistema SHALL mantenere l'identità git esistente senza modificarla
