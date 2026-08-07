# Delta spec: agent-registry-dashboard

## ADDED Requirements

### Requirement: Setup wizard multi-macchina in dashboard
Quando il git-sync non è configurato, la dashboard SHALL mostrare in evidenza una setup card che invita a configurare il multi-macchina tramite l'URL di un remote git privato. L'utente SHALL poter avviare il setup dalla dashboard tramite un endpoint dedicato che esegue pre-validazione e setup, ricevendo in UI l'esito completo: ramo applicato (inizializzazione, clone o integrazione), errori di validazione con indicazioni risolutive, e richiesta di conferma esplicita nei casi che la richiedono (repository pubblico, integrazione con dati locali).

#### Scenario: Card visibile senza sync configurato
- **WHEN** l'utente apre la dashboard e il git-sync non è configurato
- **THEN** la dashboard SHALL mostrare la setup card con il campo per l'URL remote

#### Scenario: Card assente con sync configurato
- **WHEN** il git-sync è già configurato e funzionante
- **THEN** la dashboard MUST NOT mostrare la setup card e SHALL continuare a mostrare lo stato del sync

#### Scenario: Setup riuscito dalla dashboard
- **WHEN** l'utente inserisce un URL valido e avvia il setup
- **THEN** il sistema SHALL eseguire validazione e setup, e la UI SHALL mostrare il ramo applicato e lo stato del sync aggiornato

#### Scenario: Errore di autenticazione riportato in UI
- **WHEN** la validazione fallisce per autenticazione
- **THEN** la UI SHALL mostrare l'errore con l'indicazione del meccanismo da configurare (chiave SSH o token) e MUST NOT lasciare la home in uno stato parzialmente configurato

#### Scenario: Conferma per repository pubblico
- **WHEN** il remote è un repository pubblico
- **THEN** la UI SHALL richiedere una conferma esplicita prima di completare il setup
