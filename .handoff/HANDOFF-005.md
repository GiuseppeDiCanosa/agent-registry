# HANDOFF-005 — agent-registry

> Data: 2026-08-06 02:43 | Sessione: claude-1785974583 (e claude-1785948121, chiusa nel test) | Continua da: HANDOFF-004.md
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

- **Voce corrente**: E04 — Ogni notifica porta la voce e la firma dell'agente che l'ha scritta (stato: `in-progress`)
- **Cosa stiamo facendo per il goal**: il change `agent-authored-notifications` ha tutti i task chiusi e la notifica composta è stata consegnata end-to-end su WhatsApp. Manca solo l'archiviazione perché E04 passi a `done`.
- **Resta da fare**: E02 (`approved`, change `2026-07-22-dockerize-sandbox` non archiviato), E03 (`approved`, wizard non archiviato), E04 (`in-progress`)
- **Piano**: `openspec/PLAN.md` — gate: `bash scripts/check-plan-gate.sh`

Nessuna voce è legata a un change già archiviato: nessuna staleness da segnalare.
E04 è stata **riapprovata in sessione** dopo la rinomina del change (vedi
`.handoff/HISTORY.md` alla riga `entry E04 approved → in-progress`).

## 🔄 Stato volatile

Eseguire dalla radice `/Users/giuseppedicanosa/Claude-Projects/agent-registry-oss`.
Nessun numero di questo documento va ereditato: rigenerarlo.

```bash
bash scripts/check-plan-gate.sh                                  # stato del gate
bash scripts/verify.sh | tail -3                                 # test + ownership + links
grep -c '^- \[ \]' openspec/changes/*/tasks.md                   # task aperti per change
git log --oneline origin/main..HEAD | wc -l                      # commit non pushati
git status --short | wc -l                                       # file non committati
grep -E '^### E[0-9]+|^- \*\*State\*\*:' openspec/PLAN.md | paste - -   # stati delle voci
ls -ld ~/.claude/skills/agent-registry ~/.agents/skills/agent-registry   # symlink o copie?
docker compose ps                                                # sandbox su
```

Valore non rigenerabile da comando: il messaggio WhatsApp del test end-to-end è
stato **ricevuto e confermato dall'operatore** (al 2026-08-06 01:55).

## ✅ Current Progress

### Completato ✓

- [x] **`openspec/PLAN.md` creato** con E01–E04, tutte approvate da Giuseppe con hash calcolato da `scripts/check-plan-gate.sh` (mai a mano).
- [x] **E01 done**: `scripts/check-plan-gate.sh` portato nel repo da `~/spec-as-source/scripts/`, **senza** l'header `GENERATED FROM SPEC` — coerente con gli altri tre script distribuiti dal setup, che non lo portano (l'header punterebbe a una spec inesistente in questo repo).
- [x] **Change `agent-authored-notifications`**: proposal, design, 2 delta spec, tasks — 15/15 task chiusi.
- [x] **Deposito lato registry**: `scripts/registry_manager.py` § `register_session` / `unregister_session` — parametri facoltativi `--notify-started` / `--notify-executed`, merge della mappa `notify` come per `todo`.
- [x] **Firma a runtime**: `notifier/watchdog.py` § `_sign` — la firma è il `provider` della sessione ed è l'unico punto in cui esiste; un provider nuovo firma senza modifiche al codice (test lo blinda: `Claude` e `Codex` non compaiono nel sorgente).
- [x] **Precedenza del composto + consumo distruttivo**: `notifier/watchdog.py` § `render_message` e § `consume_notify` — lock via `lock_manager`, `os.utime()` per non azzerare l'`mtime`.
- [x] **`deliver_events` estratta** da `main()`: la regola "si consuma solo dopo un invio riuscito" dentro un `while True` non era verificabile.
- [x] **Pool ripulito**: `notifier/messages.local.json` — 24 stringhe, firma `— Codex, non quello della diarrea` rimossa. Backup in `~/…/scratchpad/messages.local.json.bak`. `messages.default.json` era già senza firme.
- [x] **`SKILL.md` § "Scrivi tu il tuo messaggio"**: istruzione agli agenti, con le tre regole (non firmare, solo `started`/`executed`, facoltativo).
- [x] **Work review** requisito-per-requisito con evidenza `file:riga`; due requisiti scoperti documentati in entrambe le spec (canonica e delta).
- [x] **Test end-to-end reale**: immagine ricostruita, watchdog riavviato, sessione chiusa con messaggio composto → **consegnato su WhatsApp**, log `inviato executed (composto)`, campo `notify` consumato, `mtime` invariato.
- [x] **Sorgente unica**: `~/.claude/skills/agent-registry` e `~/.agents/skills/agent-registry` sono ora **symlink** a questo repo. Le vecchie copie sono in `~/skill-backups/`.

### In sospeso

- [ ] **Nulla è committato**: `git status --short` elenca ~10 file modificati e gli HANDOFF non tracciati. Zero commit locali oltre `origin/main`.
- [ ] **E04 non archiviata**: il change ha 0 task aperti ma non è sotto `openspec/changes/archive/`.
- [ ] **Warning del lock del watchdog**: `[lock_manager] warning: sessione 'watchdog' non trovata nel registry; lock non sincronizzato` a ogni consumo. Il lock filesystem funziona, la sincronizzazione col registry no. Nessun test lo cattura.
- [ ] **Metà del `Done when` di E04 non osservata dal vivo**: la firma `Codex` è coperta dai test unitari ma non da un invio reale.

## 💡 What Worked

- **Chiedere prima di progettare, sulla domanda giusta.** "Chi firma" aveva due letture (chi compone / chi ha causato l'evento) che portavano a due design diversi. Sciolta con l'intervista, è emerso che — siccome è l'agente stesso a comporre — le due coincidono: `firma = provider della sessione`, un solo punto nel codice invece di due rami.
- **`os.utime()` per neutralizzare la propria scrittura.** `last_activity` è l'`st_mtime` del file di sessione: il watchdog che riscrive il file per consumare il messaggio si sarebbe letto come attività dell'agente, sopprimendo l'`idle` che ha il compito di rilevare. Verificato sul file reale: `mtime` fermo all'istante del `finish`, non del consumo.
- **Estrarre la funzione per rendere testabile la regola.** `deliver_events` esiste perché "si consuma solo dopo un invio riuscito" dentro `main()` non si poteva testare. Stessa ragione per cui `classify_events` e `render_message` erano già pure.
- **Leggere la convenzione del repo invece di dedurla.** `check-target-ownership` pretende la spec canonica nello stesso diff del target; `git log --stat` su `e08602c` ha mostrato che qui si sincronizzano canonica e delta insieme durante l'implementazione. Dedotto a naso, avrei "risolto" spostando la fase.
- **Il test end-to-end trova ciò che i test non trovano.** Il warning del lock e la divergenza delle tre copie sono emersi solo eseguendo davvero.

## ❌ What Didn't Work

- **`openspec new change` rifiuta i nomi che iniziano con una cifra.** `2026-08-05-agent-authored-notifications` respinto; e la CLI rifiuta allo stesso modo il change esistente `2026-07-22-dockerize-sandbox`, che infatti è sempre stato gestito a mano. Conseguenza per E02: `openspec archive` non lo accetterà.
- **Usare la skill installata invece del repo.** Il `finish --notify-executed` è fallito con `unrecognized arguments` perché `~/.claude/skills/agent-registry` era una copia vecchia. È la ragione per cui ora sono symlink.
- **Due `verify.sh` in parallelo si contraddicono.** Un run diceva `exit=1`, l'altro `exit=0`, entrambi con 235 test verdi: si pestavano sulla stessa working dir. Verificato isolato: `EXIT=0`. Mai concludere da run concorrenti.
- **`grep -c notify` come prova di retrocompatibilità**: falso positivo, perché la stringa `working_on` della prova conteneva la parola "notify". Verificare con `yaml.safe_load` e la chiave, non con un grep sul testo.
- **Spostare le copie in `.bak` dentro `skills/`**: le carica come skill duplicata (`agent-registry.bak-20260806` è comparsa nell'elenco). Spostate in `~/skill-backups/`.

## 🚀 Next Steps

1. **Committare** — nulla di questa sessione è in git. Un commit unico con spec + target insieme (la convenzione del repo, vedi `e08602c`); gli HANDOFF sono non tracciati, decidere se includerli.
2. **Archiviare `agent-authored-notifications`** e portare E04 a `done` in `openspec/PLAN.md`, con la riga in `HISTORY.md`.
3. **Decidere il warning del lock del watchdog**: registrare `watchdog` come sessione di servizio, oppure dare a `lock_manager` un modo esplicito di prendere un lock per un processo non-sessione. Serve una voce di piano se diventa lavoro strutturato.
4. **Chiudere E02 ed E03** — entrambi debito di archiviazione, e per `2026-07-22-dockerize-sandbox` serve prima decidere se rinominarlo (riapprovando E02) o archiviarlo a mano.
5. **Bug esterno da affrontare in sessione dedicata**: `spec-as-source-setup` non distribuisce `check-plan-gate.sh` (`~/spec-as-source/skills/spec-as-source-setup/templates/` non lo contiene). Ogni progetto che fa il setup oggi nasce senza plan gate. Dettaglio completo nel context della sessione `claude-1785948121`.
6. **Osservare una notifica firmata `Codex`** dal vivo, per chiudere l'altra metà del `Done when` di E04.
