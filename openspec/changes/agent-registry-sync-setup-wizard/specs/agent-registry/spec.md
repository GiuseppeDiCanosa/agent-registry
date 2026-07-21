# Delta spec: agent-registry

## ADDED Requirements

### Requirement: Proposta del setup multi-macchina al primo avvio
Quando l'agente legge il registry all'avvio della sessione e rileva che il git-sync non è configurato, il sistema (tramite le istruzioni della skill) SHALL proporre all'utente la configurazione del multi-macchina. Se l'utente accetta, l'agente SHALL avviare la dashboard in background e aprire il browser sulla setup card; se l'utente rifiuta o rimanda, l'agente MUST NOT riproporre il setup nella stessa sessione e SHALL proseguire normalmente in modalità single-macchina.

#### Scenario: Sync non configurato
- **WHEN** l'agente legge lo stato del registry e il git-sync risulta non configurato
- **THEN** l'agente SHALL proporre all'utente il setup multi-macchina indicando che è completabile dalla dashboard

#### Scenario: Utente accetta
- **WHEN** l'utente accetta la proposta di setup
- **THEN** l'agente SHALL avviare la dashboard e aprire il browser, lasciando all'utente l'inserimento dell'URL remote nella setup card

#### Scenario: Utente rifiuta
- **WHEN** l'utente rifiuta o ignora la proposta
- **THEN** l'agente SHALL proseguire la sessione senza sync e MUST NOT riproporre il setup nella stessa sessione

#### Scenario: Porta dashboard occupata
- **WHEN** l'avvio della dashboard fallisce perché la porta di default è occupata
- **THEN** l'agente SHALL riusare l'istanza già in ascolto se è la dashboard del registry, altrimenti SHALL avviare su una porta libera e comunicare l'URL corretto
