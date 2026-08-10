# HANDOFF-006 — agent-registry

> Data: 2026-08-07 20:15 | Sessione: claude-1786111560 | Continua da: HANDOFF-005.md
> Progetto: agent-registry | Operatore: Giuseppe + Claude Opus 5

---

## 🎯 Goal

`agent-registry` coordina agenti AI eterogenei che lavorano sullo stesso progetto
e ne rende osservabile lo stato senza aprire un terminale: registry condiviso,
lock a livello filesystem, wiki delle sessioni, sandbox Docker a 5 servizi e
notifiche WhatsApp. Il repo pubblico resta installabile e privo di segreti.
(fonte: `openspec/PLAN.md` § frontmatter `goal`)

## 📍 Posizione nel piano

Letta da `openspec/PLAN.md` § Entries adesso, non a memoria:

- **Voce corrente**: nessuna. E01–E04 sono tutte `done`, `openspec/changes/`
  contiene solo `archive/`.
- **Cosa abbiamo fatto per il goal**: questa sessione non ha aggiunto capacità,
  ha chiuso il debito che il crash del Mac aveva lasciato aperto. Le tre fonti
  di verità del progetto — `PLAN.md`, `openspec/changes/`, git — erano
  disallineate: lavoro finito che risultava in corso.
- **Resta da fare**: il piano è vuoto. Il prossimo lavoro richiede una voce
  nuova, approvata, prima di qualsiasi `openspec-propose`.
- **Piano**: `openspec/PLAN.md` — gate: `bash scripts/check-plan-gate.sh`

## 🔄 Stato volatile

Eseguire dalla radice `/Users/giuseppedicanosa/Claude-Projects/agent-registry-oss`.
Nessun numero di questo documento va ereditato: rigenerarlo.

```bash
bash scripts/check-plan-gate.sh                                  # stato del gate
bash scripts/verify.sh > /tmp/v.log 2>&1; echo $?                # mai in parallelo (vedi HANDOFF-005)
ls openspec/changes/                                             # change attivi
git log --oneline origin/main..HEAD | wc -l                      # commit non pushati
git status --short | wc -l                                       # file non committati
grep -E '^### E[0-9]+|^- \*\*State\*\*:' openspec/PLAN.md | paste - -
docker compose ps                                                # sandbox su
docker compose logs wa-gateway --since 5m | tail -20             # il gateway regge?
```

Valori non rigenerabili da comando:

- Al 2026-08-07 19:39 il gateway open-wa era in **crash-loop**: `Session ready:
  <numero>` → `Session disconnected: Page crashed` ogni ~15 secondi, con
  riconnessione automatica infinita. La sessione WhatsApp è ancora autenticata
  (nessun QR da riscansionare); è il browser dentro il container che muore.
- Giuseppe ha autorizzato esplicitamente il **push pubblico** (task 6.4 di E02)
  il 2026-08-07.

## ✅ Current Progress

### Completato ✓

- [x] **`e369f90` — README, manifest, HANDOFF**: la sezione Docker del README si
  fermava a "scansiona il QR", saltando UUID di sessione (`GET /api/sessions`),
  API key e destinatario in `.env` — senza i quali il watchdog parte e non invia
  nulla. Documentato anche `AGENT_REGISTRY_DATA_SOURCE` come modo corretto di
  condividere la home, invece di editare a mano un compose generato dalla spec.
  Entrano in git gli HANDOFF 002–004, finora non tracciati.
- [x] **`a7fed81` — E03 ed E04 archiviate**. E04 con `--skip-specs` (le canoniche
  erano già sincronizzate durante l'implementazione: convenzione di questo repo,
  vedi `e08602c`). E03 senza flag, e l'archive ha applicato le sue delta creando
  **due capability nuove**.
- [x] **`agent-registry-dashboard` e `agent-registry-storage` rese spec vere**:
  nascevano da `openspec archive` con `Purpose: TBD` e **senza `targets:`**.
  Aggiunti targets (`scripts/webapp/main.py` + `static/index.html`;
  `scripts/sync_manager.py`), Purpose, e un `**Verified by**` per requisito verso
  i test che già coprivano il wizard (`tests/test_webapp.py` 27 test,
  `tests/test_sync_manager.py` 39). Header `GENERATED FROM SPEC` sui tre file.
- [x] **`224ae1b` — piano riallineato**: E02, E03, E04 tutte `done`;
  `check-plan-gate.sh` **PASSED (0 active change(s), 4 plan entries)**.
- [x] **E02 archiviata** con `--skip-specs` e la directory rinominata a mano in
  `2026-07-22-dockerize-sandbox`: il `Done when` della voce verifica quel path
  letterale e `openspec` avrebbe prodotto `2026-08-07-2026-07-22-dockerize-sandbox`.
- [x] **`verify.sh` verde** a ogni passaggio: 235 test, `check-spec-links` e
  `check-target-ownership` PASSED.

### In sospeso

- [ ] **Gateway open-wa: crash-loop il 2026-08-07, tornato a consegnare dopo.**
  Ipotesi mai verificata: `/dev/shm` troppo piccolo per Chromium (il default
  Docker è 64 MB; il compose non imposta `shm_size`). La diagnosi è stata
  interrotta su richiesta di Giuseppe, che dice di aver trovato la causa —
  **chiedergli quale prima di reinvestigare**. A fine sessione il log mostra
  `inviato started (composto) -> claude-1786372837`, quindi la catena funziona di
  nuovo; non è noto se per un intervento o da sé.
- [ ] **Firma `Codex` mai osservata dal vivo**: metà del `Done when` di E04.
  Coperta dai test unitari, non da un invio reale. Il tentativo del 2026-08-07 è
  fallito sul gateway, non sul codice.
- [ ] **BUG: un invio fallito fa perdere l'evento per sempre.** Trovato a fine
  sessione con evidenza diretta. `notifier/watchdog.py:322-327`:

  ```python
  events, state = classify_events(...)
  deliver_events(events, home, pool, ...)   # può fallire
  _save_state(state_path, state)            # salva comunque
  ```

  Lo stato avanza anche quando `deliver_events` non ha spedito nulla. Il commento
  a `watchdog.py:279` dichiara "Il testo composto resta nel file: riparte al ciclo
  successivo", ma non riparte: `classify_events` genera l'evento dal *confronto*
  fra stato salvato e stato attuale, e lo stato salvato è già avanzato. Il testo
  composto resta nel registry per sempre, mai spedito.

  Evidenza: la sessione `claude-1786111560` di questo handoff. `started` fallito
  con `HTTP 409` durante il crash-loop del gateway, `.watchdog-state.json` la
  riporta comunque `Finished`, e il suo file di sessione ha ancora **entrambi** i
  testi in `notify` non consumati. Nessun test lo cattura, perché
  `deliver_events` è testata isolata dal salvataggio dello stato.

  Fix probabile: salvare lo stato solo per gli eventi effettivamente consegnati,
  cioè far tornare a `deliver_events` l'insieme dei recapiti riusciti. Ma
  `watchdog.py` è un `targets` di `whatsapp-notifications`: serve prima la spec e
  una voce di piano.
- [ ] **Warning del lock del watchdog**, ereditato da HANDOFF-005 e non
  affrontato: `[lock_manager] warning: sessione 'watchdog' non trovata nel
  registry; lock non sincronizzato` a ogni consumo. Il lock filesystem funziona,
  la sincronizzazione col registry no. Nessun test lo cattura.
- [ ] **Bug esterno**: `spec-as-source-setup` non distribuisce
  `check-plan-gate.sh`. Ogni progetto che fa il setup oggi nasce senza plan gate.
  Riguarda `~/spec-as-source`, non questo repo.

## 💡 What Worked

- **Ricostruire dal disco invece di fidarsi dell'handoff.** HANDOFF-005 diceva
  "Nulla è committato: zero commit locali oltre `origin/main`". Falso: `a6b5665`
  esisteva già. L'handoff era stato scritto *prima* del commit e il crash ha
  impedito l'aggiornamento. Un handoff descrive l'intenzione al momento della
  scrittura, non lo stato — `git log` sì.
- **Guardare cosa produce `openspec archive`, non solo il suo exit code.** Ha
  detto "Specs updated successfully" e ha creato due spec inservibili: senza
  `targets`, la regola "non tocchi il file senza toccare la spec" non ha presa su
  `sync_manager.py` e `webapp/main.py`. Il comando era riuscito, il risultato no.
- **Il rifiuto di `archive` è informazione.** Su E04 ha abortito con "already
  exists" invece di duplicare: è il segnale che le canoniche erano già a posto e
  che serviva `--skip-specs`, non che qualcosa fosse rotto.
- **Verificare che i test citati coprano davvero il requisito.** Prima di
  scrivere `**Verified by**: tests/test_webapp.py` ho cercato i nomi dei test:
  `test_setup_card_present_in_index_html`, `test_sync_init_needs_confirm_public`.
  Un `Verified by` verso un file che non copre il requisito passa `verify.sh` e
  mente.
- **Scrivere la riserva nel piano.** E04 è `done` con metà del `Done when` non
  osservata: sta scritto nella voce, con la ragione (gateway) e il perché il
  codice non è in discussione (`_sign` è un punto solo). Fra un mese la domanda
  "ma l'abbiamo davvero visto?" ha già la risposta.

## ❌ What Didn't Work

- **`openspec archive` su nomi che iniziano con una data.** Ha prodotto
  `2026-08-07-2026-07-22-dockerize-sandbox`, rompendo il path che il `Done when`
  di E02 verifica letteralmente. Rinominato a mano. Coerente con quanto già noto
  da HANDOFF-005: la CLI e i nomi con cifra iniziale non vanno d'accordo.
- **Chiudere una sessione Codex per far uscire una notifica.** Registrata
  `codex-1786131550` e chiusa: il watchdog ha risposto `HTTP 409: Session is not
  connected`. Il test dal vivo richiede che il gateway sia sano — verificarlo
  *prima* (`docker compose logs wa-gateway`), non dedurlo dal fatto che il
  container è `Up`. Lo era, ed era `healthy`: l'healthcheck non vede il crash del
  browser interno.
- **`docker stats` come indizio di salute**: 520 MB e 1.4% di CPU sembravano
  normali. Il crash-loop si vede solo nei log.
- **Dare per chiusa una sessione senza guardare se la notifica è uscita.** Il
  `finish` ha stampato "Sessione terminata" e il registry dice `Finished`: tutto
  a posto in apparenza. Solo controllando il campo `notify` sul file di sessione
  è emerso che i due testi erano ancora lì, mai spediti — ed è così che è stato
  trovato il bug dell'evento perso. Il comando che riesce non dice nulla
  sull'effetto che doveva produrre.

## 🚀 Next Steps

1. **Voce di piano per il bug dell'evento perso** (vedi § In sospeso). È il
   candidato più forte al prossimo lavoro: rende inaffidabile l'intera catena di
   notifica proprio quando il gateway ha un problema — cioè quando la notifica
   serve di più.
2. **Chiedere a Giuseppe la causa del crash del gateway** — dice di averla
   trovata, il gateway è tornato a consegnare (`inviato started (composto) ->
   claude-1786372837` nei log). Se serve un fix nel compose, quello è un file
   `targets` di `container-deployment`: richiede spec e voce di piano, non una
   modifica diretta.
3. **Osservare una notifica firmata `Codex`** appena il gateway regge, e togliere
   la riserva dalla voce E04.
4. **Decidere il warning del lock del watchdog**: registrare `watchdog` come
   sessione di servizio, oppure dare a `lock_manager` un modo esplicito di
   prendere un lock per un processo non-sessione.
5. **Il piano è vuoto**: qualsiasi lavoro nuovo parte da `plan-mode` con una voce
   approvata, non da `openspec-propose`.
