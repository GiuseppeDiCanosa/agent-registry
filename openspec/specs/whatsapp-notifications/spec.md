---
targets:
  - notifier/watchdog.py
  - notifier/wa_client.py
---

# whatsapp-notifications Specification

## Purpose
Notificare l'operatore via WhatsApp quando lo stato degli agenti nel registry cambia in modi
rilevanti: un agente ha avviato una sessione di lavoro, un agente ha completato il lavoro, un
agente si è fermato, o un agente è rimasto inattivo troppo a lungo. I messaggi provengono da un
pool configurabile e sono scelti a caso
per varietà. L'integrazione WhatsApp avviene tramite un gateway HTTP esterno (open-wa) e non
richiede modifiche agli script del registry: il watchdog osserva soltanto lo stato in
`AGENT_REGISTRY_HOME`.

## Requirements

### Requirement: Rilevamento dei quattro eventi di notifica
Il watchdog SHALL classificare le sessioni in quattro eventi — `started` (una sessione passa a
`OnWorking` da uno stato precedente diverso, inclusa la prima comparsa nel registry), `executed`
(una sessione passa a `Finished`), `stopped` (una sessione passa a `Stop` o `Killed`), `idle`
(una sessione `OnWorking` senza attività da oltre una soglia configurabile, default 3600s) —
usando lo stato precedente per emettere ogni evento **una sola volta** per transizione.

**Verified by**: [@test] tests/notifier/test_watchdog.py

#### Scenario: avvio sessione rilevato una sola volta
- **WHEN** una sessione compare nel registry con status `OnWorking` (o vi transita da uno stato diverso) fra due cicli
- **THEN** viene emesso un evento `started` per quella sessione
- **AND** al ciclo successivo, se lo stato resta `OnWorking`, non viene emesso di nuovo

#### Scenario: completamento rilevato una sola volta
- **WHEN** una sessione passa da `OnWorking` a `Finished` fra due cicli
- **THEN** viene emesso un evento `executed` per quella sessione
- **AND** al ciclo successivo, se lo stato resta `Finished`, non viene emesso di nuovo

#### Scenario: arresto rilevato
- **WHEN** una sessione passa a `Stop` o `Killed`
- **THEN** viene emesso un evento `stopped` per quella sessione una sola volta

#### Scenario: inattività oltre la soglia
- **WHEN** una sessione è `OnWorking` e la sua ultima attività è più vecchia della soglia idle
- **THEN** viene emesso un evento `idle` una sola volta
- **AND** finché resta idle senza tornare attiva non vengono emessi ulteriori eventi `idle`

### Requirement: Avvio a freddo senza notifiche storiche
Al primo ciclo su un registry già popolato (nessuno stato precedente persistito), il watchdog
SHALL registrare lo stato corrente senza emettere alcun evento; solo i cambiamenti nei cicli
successivi SHALL generare notifiche.

**Verified by**: [@test] tests/notifier/test_watchdog.py

#### Scenario: nessun flood all'avvio
- **WHEN** il watchdog classifica per la prima volta (cold start) sessioni già `Finished` o idle
- **THEN** non emette alcun evento
- **AND** al ciclo successivo un cambiamento nuovo genera regolarmente il suo evento

### Requirement: Messaggi da pool con placeholder
Il watchdog SHALL scegliere a caso un messaggio dal pool dell'evento e sostituire i
placeholder disponibili (`{name}`, `{session_id}`, `{provider}`, `{working_on}`, e `{minutes}`
per gli eventi idle). SHALL preferire un pool locale (`notifier/messages.local.json`) se
presente, altrimenti il pool di default committato.

**Verified by**: [@test] tests/notifier/test_watchdog.py

#### Scenario: rendering di un messaggio idle
- **WHEN** si renderizza un evento `idle` con nome e minuti noti
- **THEN** il testo risultante contiene il nome e i minuti al posto dei placeholder
- **AND** non restano placeholder non sostituiti fra quelli disponibili

### Requirement: Invio via gateway esterno senza segreti hardcoded
L'invio SHALL avvenire con una POST HTTP al gateway open-wa (endpoint send-text), con URL,
API key e destinatario forniti da configurazione/ambiente. Il codice NON SHALL contenere il
numero destinatario né la API key in chiaro.

**Verified by**: [@test] tests/notifier/test_watchdog.py

#### Scenario: la richiesta di invio è costruita da configurazione
- **WHEN** si prepara l'invio di un messaggio a un destinatario configurato
- **THEN** la POST punta all'endpoint send-text del gateway con il testo e il destinatario dati
- **AND** destinatario e API key provengono da parametri/ambiente, non da costanti nel codice
