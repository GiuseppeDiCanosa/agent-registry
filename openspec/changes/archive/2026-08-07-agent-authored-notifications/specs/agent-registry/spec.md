---
targets:
  - scripts/registry_manager.py
---

## ADDED Requirements

### Requirement: Deposito del messaggio di notifica composto dall'agente
Il registry SHALL permettere a un agente di depositare, nel proprio file di
sessione, il testo di notifica che ha composto per un evento, sotto una mappa
`notify` con una chiave per evento. La CLI SHALL accettare quel testo al momento
di registrare e di chiudere una sessione tramite parametri **facoltativi**: una
chiamata che non li passa SHALL comportarsi esattamente come prima del change, e
nessun comando SHALL fallire per la loro assenza. Il deposito SHALL usare lo
stesso percorso di scrittura concorrente degli altri campi di sessione, così che
un deposito non possa perdere un aggiornamento fatto in parallelo.

**Verified by**: [@test] tests/test_registry_manager.py

#### Scenario: deposito al momento della registrazione
- **WHEN** una sessione viene registrata passando il testo per l'evento `started`
- **THEN** il file di sessione contiene quel testo sotto `notify`, alla chiave dell'evento

#### Scenario: deposito alla chiusura
- **WHEN** una sessione viene chiusa passando il testo per l'evento `executed`
- **THEN** il file di sessione contiene quel testo sotto `notify`, alla chiave dell'evento

#### Scenario: i parametri restano facoltativi
- **WHEN** una sessione viene registrata o chiusa senza passare alcun testo
- **THEN** il comando riesce con lo stesso esito che aveva prima del change
- **AND** il file di sessione non contiene la mappa `notify`

#### Scenario: il deposito non cancella gli altri eventi
- **WHEN** una sessione che ha già un testo per un evento ne deposita uno per un evento diverso
- **THEN** la mappa `notify` contiene entrambi i testi
