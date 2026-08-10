---
targets:
  - notifier/watchdog.py
  - notifier/wa_client.py
---

## MODIFIED Requirements

### Requirement: Messaggi da pool con placeholder
Il watchdog SHALL comporre il testo di una notifica per due percorsi, nell'ordine:
se il file di sessione dell'agente contiene un testo composto per l'evento
rilevato, SHALL usare quel testo verbatim; altrimenti SHALL scegliere a caso un
messaggio dal pool dell'evento e sostituire i placeholder disponibili (`{name}`,
`{session_id}`, `{provider}`, `{working_on}`, e `{minutes}` per gli eventi idle).
SHALL preferire un pool locale (`notifier/messages.local.json`) se presente,
altrimenti il pool di default committato. In entrambi i percorsi SHALL apporre al
testo la firma del `provider` della sessione a cui l'evento si riferisce; le
stringhe del pool NON SHALL contenere una firma al proprio interno, così che un
provider mai visto prima firmi col proprio nome senza modifiche al codice.

**Verified by**: [@test] tests/notifier/test_watchdog.py

#### Scenario: rendering di un messaggio idle
- **WHEN** si renderizza un evento `idle` con nome e minuti noti
- **THEN** il testo risultante contiene il nome e i minuti al posto dei placeholder
- **AND** non restano placeholder non sostituiti fra quelli disponibili

#### Scenario: il testo composto dall'agente ha la precedenza
- **WHEN** si renderizza un evento per una sessione il cui file dichiara un testo composto per quell'evento
- **THEN** il messaggio inviato contiene quel testo verbatim
- **AND** nessuna stringa del pool viene usata per quell'invio

#### Scenario: la firma è quella del provider della sessione
- **WHEN** si renderizza un evento per una sessione con `provider` valorizzato, per entrambi i percorsi
- **THEN** il testo risultante termina con la firma di quel provider
- **AND** un provider mai visto prima produce la propria firma senza modifiche al codice

#### Scenario: il pool di default non contiene firme
- **WHEN** si carica `notifier/messages.default.json`
- **THEN** nessuna delle sue stringhe contiene una firma di provider

## ADDED Requirements

### Requirement: Consumo del messaggio composto senza alterare l'inattività
Dopo aver inviato un testo composto dall'agente, il watchdog SHALL rimuoverlo dal
file di sessione, così che una composizione produca esattamente un invio. La
rimozione SHALL avvenire dopo aver acquisito il lock del file tramite
`lock_manager`, rilasciandolo al termine, perché il file appartiene all'agente che
può scriverlo nello stesso momento. Il watchdog SHALL ripristinare l'`mtime` che
il file aveva prima della riscrittura: l'`mtime` è la sorgente di
`last_activity`, e una riscrittura che lo aggiornasse verrebbe letta come
attività dell'agente, sopprimendo l'evento `idle` che il watchdog ha il compito
di rilevare. Un fallimento nell'acquisire il lock NON SHALL impedire l'invio già
avvenuto né interrompere il ciclo.

**Verified by**: [@test] tests/notifier/test_watchdog.py

#### Scenario: un solo invio per composizione
- **WHEN** un testo composto viene inviato e il ciclo successivo rileva di nuovo lo stesso stato
- **THEN** quel testo non è più nel file di sessione
- **AND** non viene inviato una seconda volta

#### Scenario: l'mtime sopravvive al consumo
- **WHEN** il watchdog rimuove un testo composto dal file di sessione
- **THEN** l'`mtime` del file è identico a quello che aveva prima della rimozione

#### Scenario: il lock non è disponibile
- **WHEN** il lock del file di sessione è tenuto da un altro processo al momento del consumo
- **THEN** il ciclo del watchdog prosegue senza sollevare eccezioni

### Requirement: Degradazione sicura della firma e del consumo
Il watchdog SHALL restare operativo quando i dati di una sessione sono incompleti o il suo file non è scrivibile. Se la sessione non dichiara un `provider`, il testo SHALL essere inviato senza firma anziché con una firma vuota o inventata. Il consumo SHALL avvenire solo dopo un invio riuscito, così che un messaggio non spedito resti disponibile per il ciclo successivo. Se il file di sessione non esiste più al momento del consumo, l'operazione SHALL terminare senza errore. Quando la rimozione lascia la mappa `notify` vuota, il watchdog SHALL rimuovere anche la mappa, per non lasciare residui nel file.

**Verified by**: [@test] tests/notifier/test_watchdog.py

#### Scenario: sessione senza provider
- **WHEN** si renderizza un evento per una sessione priva del campo `provider`
- **THEN** il testo inviato non porta alcuna firma

#### Scenario: invio fallito, messaggio conservato
- **WHEN** l'invio al gateway fallisce per un testo composto
- **THEN** il testo resta nel file di sessione

#### Scenario: file di sessione sparito
- **WHEN** il consumo viene tentato su una sessione il cui file non esiste
- **THEN** l'operazione riporta di non aver rimosso nulla, senza sollevare

#### Scenario: nessun residuo dopo l'ultimo consumo
- **WHEN** viene consumato l'ultimo testo rimasto nella mappa `notify`
- **THEN** il file di sessione non contiene più la mappa `notify`
