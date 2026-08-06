---
goal: >
  agent-registry coordina agenti AI eterogenei che lavorano sullo stesso progetto
  e ne rende osservabile lo stato senza aprire un terminale: registry condiviso,
  lock a livello filesystem, wiki delle sessioni, sandbox Docker a 5 servizi e
  notifiche WhatsApp. Il repo pubblico resta installabile e privo di segreti.
done-when: >
  Un operatore installa la skill dal repo pubblico, avvia la sandbox con un
  comando e riceve su WhatsApp gli eventi di ogni agente attivo sulla sua
  macchina, con messaggi scritti dagli agenti stessi. `bash scripts/verify.sh` e
  `bash scripts/check-plan-gate.sh` escono 0, e nessun change attivo resta fuori
  dal piano.
plan-version: 1
---

# PLAN — agent-registry

> Il piano è la sorgente della sequenza dei change, come la spec è la sorgente del
> codice. Nessun `openspec-propose` senza una voce qui, approvata.
> Gate meccanico: `bash scripts/check-plan-gate.sh`.

## Entries

### E01 — Il gate del piano è eseguibile in questo repo

- **Change**: none
- **Depends on**: —
- **Done when**: `bash scripts/check-plan-gate.sh` eseguito dalla radice del repo
  stampa un verdetto per ogni directory sotto `openspec/changes/` diversa da
  `archive/`, invece di fallire con `No such file or directory`; e
  `bash scripts/check-plan-gate.sh --hash E01` stampa una stringa `sha256:` di 16
  cifre esadecimali.
- **State**: done
- **Approved by**: Giuseppe Di Canosa
- **Approved at**: 2026-08-05 19:04
- **Approval hash**: sha256:7b002b3645a6f33b

Il repo ha `scripts/verify.sh` e `openspec/schemas/`, ma non
`scripts/check-plan-gate.sh`: lo script esiste solo in `~/spec-as-source`. Finché
manca, il gate non è verificabile e gli hash di approvazione delle voci qui sotto
non sono calcolabili — vanno riempiti dopo E01, mai a mano.

### E02 — La sandbox Docker è chiusa e archiviata

- **Change**: 2026-07-22-dockerize-sandbox
- **Depends on**: E01
- **Done when**: `grep -c '^- \[ \]' openspec/changes/archive/2026-07-22-dockerize-sandbox/tasks.md`
  stampa `0` e la directory esiste sotto `archive/` — cioè `verify.sh` verde,
  `work-review` fatta, README/SKILL con la sezione di avvio via Docker, e il push
  pubblico eseguito o esplicitamente rinunciato.
- **State**: approved
- **Approved by**: Giuseppe Di Canosa
- **Approved at**: 2026-08-05 19:04
- **Approval hash**: sha256:addce8bde7d925a5

**Voce retroattiva, e vale la pena dirlo invece di normalizzarlo**: questo change
è partito e ha prodotto una release (v0.4.0, v0.5.0) prima che il piano
esistesse. Senza questa voce il gate risponderà `NO-ENTRY` su di lui appena E01
è in piedi. Restano aperti i task 6.1–6.4 di `tasks.md`.

### E03 — Il wizard di setup del sync è archiviato

- **Change**: agent-registry-sync-setup-wizard
- **Depends on**: E01
- **Done when**: la directory è sotto `openspec/changes/archive/` e ogni riga
  `### Requirement:` dei suoi delta compare identica nella spec canonica
  corrispondente sotto `openspec/specs/`.
- **State**: approved
- **Approved by**: Giuseppe Di Canosa
- **Approved at**: 2026-08-05 19:04
- **Approval hash**: sha256:f6a700bbc079fe96

Anche questa retroattiva: 26 task su 26 completati e `work-review.md` presente,
ma il change non è mai stato archiviato in questo clone. È debito di chiusura,
non lavoro nuovo.

### E04 — Ogni notifica porta la voce e la firma dell'agente che l'ha scritta

- **Change**: agent-authored-notifications
- **Depends on**: E01
- **Done when**: una sessione Claude che arriva a `Finished` produce una notifica
  firmata `Claude` e una sessione Codex una firmata `Codex`, senza che il
  watchdog contatti alcuna API LLM (verificabile con il container senza rete
  verso l'esterno); e nessuna stringa di `notifier/messages.default.json`
  contiene una firma hardcoded.
- **State**: in-progress
- **Approved by**: Giuseppe Di Canosa
- **Approved at**: 2026-08-05 19:47
- **Approval hash**: sha256:5f5aaf59296c001e

Il contratto deciso in sessione, che il change dovrà specificare:

- **Chi scrive firma.** La firma identifica l'agente che ha *composto* il testo,
  non quello che ha fatto scattare l'evento. Oggi le 24 stringhe di
  `messages.local.json` finiscono tutte con `— Codex, non quello della diarrea`,
  quindi anche gli eventi delle sessioni Claude si firmano Codex.
- **Nessuna chiamata API dal watchdog.** Sono gli agenti stessi, mentre lavorano,
  a comporre il proprio messaggio e a depositarlo nel registry; il watchdog lo
  raccoglie e lo spedisce. Niente chiave, niente costo per messaggio, nessuna
  dipendenza di rete in più nel container.
- **Due eventi su quattro non sono componibili dall'agente.** `started` ed
  `executed` sì — l'agente è vivo in quel momento. `stopped` e `idle` no, per
  definizione: lì l'agente è fermo o morto e non può scrivere nulla. Quei due
  restano sempre sul percorso di fallback.
- **Fallback = pool statico con firma dinamica.** Quando manca un messaggio
  composto, si pesca da `messages.*.json` come oggi, ma la firma va tolta dalle
  stringhe e applicata a runtime. La notifica arriva sempre: un guasto è
  esattamente il momento in cui serve sapere che un agente si è fermato.
- **Terzo provider senza modifiche al codice.** L'insieme dei firmatari non è una
  costante nel sorgente: un provider nuovo che registra una sessione firma col
  proprio nome senza toccare `watchdog.py`.
