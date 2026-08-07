## Context

Il watchdog (`notifier/watchdog.py`) osserva `<AGENT_REGISTRY_HOME>/sessions/*.yaml`
a intervalli di `WATCHDOG_INTERVAL` (default 60s), classifica quattro eventi
(`started`, `executed`, `stopped`, `idle`) confrontando lo stato corrente con
quello persistito in `.watchdog-state.json`, sceglie un messaggio con
`random.choice` dal pool e lo spedisce al gateway open-wa. Oggi legge soltanto:
non ha mai scritto in un file di sessione. `last_activity` non è un campo
dichiarato ma l'`st_mtime` del file, ed è ciò che decide l'evento `idle`.

Le stringhe del pool locale portano la firma dentro al testo, perché furono
scritte da una sessione Codex a cui era stato chiesto di firmarsi. Il risultato è
che ogni notifica, di qualunque provider, arriva firmata Codex.

Gli agenti che potrebbero comporre quei testi sono già in esecuzione e già
scrivono nel registry tramite `registry_manager.py`. Il contratto di questo
change è fissato dal prompt-loop (7 lock) e dalla voce E04 del piano, approvata
con hash `sha256:5f5aaf59296c001e`.

## Goals / Non-Goals

**Goals:**

- Il testo di una notifica, quando esiste, è quello che l'agente ha composto con
  la propria voce.
- La firma dice il vero su chi ha prodotto il messaggio, per qualunque provider,
  presente o futuro.
- Nessuna chiamata ad API LLM dal watchdog: niente chiavi, niente costo per
  messaggio, nessuna dipendenza di rete nuova nel container.
- Compatibilità all'indietro totale con le chiamate CLI esistenti.

**Non-Goals:**

- Enforcement. Nessun comando fallisce se il messaggio manca.
- Composizione per `stopped` e `idle`.
- Dashboard e webapp.
- Ripulire il pool locale nel repo: `messages.local.json` è gitignored.

## Decisions

### D1 — Il messaggio vive in un campo `notify` del file di sessione

L'agente scrive in `sessions/<id>.yaml` una mappa `notify` con una chiave per
evento. Il watchdog legge già quei file a ogni ciclo: nessuna infrastruttura
nuova, nessun percorso da configurare, e il messaggio è ispezionabile accanto
allo stato che lo motiva.

*Alternative considerate.* Una coda `outbox/` con un comando CLI dedicato:
elimina ogni interferenza con l'mtime e rende visibile cosa è in attesa, ma
aggiunge una directory e un comando da mantenere. Il context file di sessione:
nessun comando nuovo, ma è prosa libera destinata alla wiki, e il watchdog
dovrebbe estrarre un messaggio da testo non strutturato — con il rischio che una
riga di log finisca su WhatsApp.

### D2 — Consumo distruttivo, sotto lock, con mtime ripristinato

Dopo l'invio il watchdog rimuove la chiave dal file: un invio per composizione,
senza stato aggiuntivo da mantenere altrove. Questo è il punto di rischio del
change, perché il watchdog scrive per la prima volta in un file di proprietà
degli agenti, e può farlo mentre l'agente sta scrivendo lo stesso file.

Due mitigazioni, entrambe obbligatorie:

1. La riscrittura avviene dopo aver acquisito il lock via `lock_manager`, e lo
   rilascia subito dopo.
2. L'`st_mtime` precedente viene ripristinato con `os.utime()`. Senza questo, la
   riscrittura fatta dal watchdog verrebbe letta al ciclo successivo come
   attività dell'agente e azzererebbe il conto dell'inattività: il watchdog
   renderebbe invisibile l'evento `idle` che ha il compito di rilevare.

*Alternative considerate.* Tenere il file in sola lettura e segnare l'avvenuto
invio in `.watchdog-state.json`, come già avviene per gli eventi: niente lock,
niente mtime da salvare, file di sessione di proprietà esclusiva dell'agente. È
la soluzione tecnicamente più semplice ed è stata scartata per scelta esplicita
dell'operatore. Una scadenza `composed_at` con TTL: evita che un testo di
stamattina arrivi stasera, ma non è stata richiesta.

### D3 — `firma = provider della sessione`, su entrambi i percorsi

La firma non è più parte del template: viene applicata a runtime leggendo il
`provider` della sessione. Le due regole lockate — "firma chi ha scritto" (L1) e
"nel fallback firma il provider della sessione" (L6) — non divergono, perché è
l'agente stesso a comporre il proprio messaggio: il compositore *è* il provider
della sessione. Ne consegue un solo punto di applicazione nel codice e un terzo
provider che firma col proprio nome senza che `watchdog.py` cambi.

*Alternativa considerata.* Firmare il fallback come `watchdog`, per distinguere a
colpo d'occhio le notifiche automatiche da quelle scritte da un agente vivo:
scartata perché uniformare l'aspetto è stato preferito alla distinzione.

### D4 — L'innesco è un'istruzione, non un vincolo

I parametri CLI sono facoltativi; la `SKILL.md` istruisce gli agenti a comporre.
Un agente che non lo fa si degrada sul pool statico senza errori.

*Alternativa considerata.* Rendere il parametro obbligatorio su `register` e
`finish`: garantirebbe la composizione, ma romperebbe ogni chiamata esistente —
comprese quelle delle sessioni Codex in corso — e renderebbe impossibile
registrarsi da script o a mano.

## Risks / Trade-offs

- **Il watchdog scrive dove finora leggeva** → lock via `lock_manager` +
  ripristino dell'mtime (D2). È il rischio principale del change.
- **Degradazione silenziosa**: un agente che non compone non produce alcun
  segnale, e la feature può risultare inattiva senza che nessuno se ne accorga →
  accettato per scelta (D4). Il log del watchdog che distingue i due percorsi
  resta disponibile come mitigazione futura, non implementata qui.
- **`messages.local.json` è gitignored**: la ripulitura delle sue 24 stringhe
  vale solo su questa macchina; una reinstallazione pulita riparte dal pool di
  default → il default va lasciato senza firme, così il caso base è corretto.
- **Il testo composto non è validato**: arriva da un agente e va dritto al
  gateway → vedi Open Questions.

## Migration Plan

Nessuna migrazione dati. I file di sessione senza campo `notify` sono già lo
stato valido di partenza e restano validi: il percorso di fallback è il
comportamento odierno, invariato. Rollback = revert del commit; le eventuali
chiavi `notify` rimaste nei file vengono semplicemente ignorate dal watchdog
precedente.

## Open Questions

Tre casi limite restano deliberatamente non decisi — sono stati sollevati durante
il prompt-loop e marcati `n/a` invece di essere risolti d'ufficio:

1. **Testo troppo lungo o con caratteri che rompono il gateway open-wa.** Serve
   un limite di lunghezza e una sanificazione, o si accetta il testo verbatim?
2. **Messaggio composto per un evento che non scatta mai.** Con il consumo
   distruttivo la chiave resta nel file a tempo indefinito. Va bene, o serve una
   scadenza?
3. **Campi `notify` già presenti al cold start.** Il primo ciclo semina lo stato
   senza emettere eventi, quindi non li consuma: restano lì. Comportamento
   corretto o da gestire?
