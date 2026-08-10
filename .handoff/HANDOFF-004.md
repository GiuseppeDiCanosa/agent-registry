# HANDOFF-004 — agent-registry

> Data: 2026-08-06 02:33 | Sessione: codex-1785976342 | Continua da: HANDOFF-003.md
> Progetto: agent-registry | Operatore: Giuseppe + Codex (GPT-5)

## 📍 Posizione nel piano

La sorgente canonica è `/Users/giuseppedicanosa/Claude-Projects/agent-registry-oss`.
Il piano corrente è `openspec/PLAN.md` § Entries:

- **E01 — Il gate del piano è eseguibile in questo repo**: `done`.
- **E02 — La sandbox Docker è chiusa e archiviata**: `approved`, change
  `2026-07-22-dockerize-sandbox`. I task tecnici e di review sono completati;
  resta il task di release pubblico 6.4 e il change non è ancora archiviato.
- **E03 — Il wizard di setup del sync è archiviato**: `approved`, ma il change
  è ancora presente fuori da `openspec/changes/archive/`; è debito di chiusura,
  non lavoro nuovo (`openspec/PLAN.md` § E03).
- **E04 — Ogni notifica porta la voce e la firma dell’agente che l’ha scritta**:
  `in-progress`, change `agent-authored-notifications`; i task del change sono
  completi ma il change non è ancora stato archiviato (`openspec/PLAN.md` § E04).

Questo handoff non avvia Neo4j. Il vecchio `migrate-wiki-to-neo4j` di HANDOFF-002
non è presente nel piano o nei change attivi della sorgente canonica. Neo4j
richiede una nuova voce di piano approvata, poi proposal/design/spec/tasks e
implementazione separata.

## 🎯 Goal

Rendere `agent-registry` una skill pubblica installabile che coordina agenti AI
eterogenei, rende osservabile il loro stato tramite registry/lock/wiki, offre una
sandbox Docker a cinque servizi e invia notifiche WhatsApp con testo composto dagli
agenti, mantenendo il repository pubblico privo di segreti (`openspec/PLAN.md` §
frontmatter `goal` e `done-when`).

## 🔄 Stato volatile

Eseguire dalla radice `/Users/giuseppedicanosa/Claude-Projects/agent-registry-oss`.
I valori numerici vanno rigenerati, non copiati da questo snapshot:

```bash
bash scripts/check-plan-gate.sh
openspec list --json
openspec status --change agent-authored-notifications --json
grep -c '^- \[ \]' openspec/changes/2026-07-22-dockerize-sandbox/tasks.md
grep -c '^- \[ \]' openspec/changes/agent-authored-notifications/tasks.md
git status --short
git log --oneline origin/main..HEAD | wc -l
python3 scripts/registry_manager.py status --project agent-registry
```

Il comando `openspec status --change 2026-07-22-dockerize-sandbox --json` è
conosciuto come non applicabile: la CLI rifiuta i nomi di change che iniziano con
una cifra. Usare `openspec list --json` e i file artifact per questo change, senza
rinominarlo retroattivamente.

Per riverificare l’implementazione prima di dichiarare una chiusura:

```bash
bash scripts/verify.sh
bash scripts/check-plan-gate.sh
```

Per controllare i lock prima di toccare documentazione o artifact:

```bash
python3 scripts/lock_manager.py check README.md SKILL.md \
  openspec/changes/2026-07-22-dockerize-sandbox/tasks.md \
  .handoff/HISTORY.md .spec-source-manifest.json \
  --session-id <session-id-corrente>
```

Il git-sync del registry va controllato con:

```bash
python3 scripts/sync_manager.py status
```

Un pull/rebase automatico può restare sospeso quando il repository contiene
modifiche unstaged; non fare stash/reset automatici e non sovrascrivere il lavoro
locale.

## ✅ Current Progress

### Completato

- [x] La sorgente unica operativa è il repo canonico
  `/Users/giuseppedicanosa/Claude-Projects/agent-registry-oss`; la copia
  `.agents/skills/agent-registry` punta al repo tramite symlink. La directory
  `.codex/skills/agent-registry` può ancora essere una copia storica: non usarla
  come sorgente di modifica (`CLAUDE.md` § Chi sono / Contesto; verifica `readlink`).
- [x] E01, il gate locale, è `done` e `scripts/check-plan-gate.sh` è presente
  (`openspec/PLAN.md` § E01).
- [x] E02 task 6.1: `scripts/verify.sh` eseguito con check spec-link e target
  ownership verdi e suite completa verde; l’esito rigenerabile è nel blocco
  `Stato volatile` e il log è registrato in `.handoff/HISTORY.md`.
- [x] E02 task 6.2: work-review manuale requisito-per-requisito completata per le
  sette capability container e le quattro capability WhatsApp; le evidenze sono
  `docker-compose.yml`, `docker/Dockerfile`, `docker/sync-loop.sh`,
  `notifier/watchdog.py`, `notifier/wa_client.py` e i test collegati.
- [x] E02 task 6.3: `README.md` § Sandbox Docker e `SKILL.md` § Avvio descrivono
  l’avvio staged: base sandbox, QR open-wa, recupero UUID/API key/recipient e
  avvio del watchdog. La configurazione della home host usa
  `AGENT_REGISTRY_DATA_SOURCE` senza modificare il compose generato.
- [x] E04 è stato applicato dal lavoro parallelo e le sue modifiche locali sono
  state preservate: `notifier/watchdog.py`, `scripts/registry_manager.py`,
  spec WhatsApp/agent-registry e test correlati restano nel worktree.
- [x] La verifica finale dopo la documentazione è stata eseguita; l’ultimo log
  operativo è in `.handoff/HISTORY.md` § righe `2026-08-06 02:17`–`02:24`.

### Sospeso / non eseguito

- [ ] E02 task 6.4: bump di versione, commit e push pubblico. Non eseguirli senza
  conferma esplicita dell’utente; il worktree contiene anche modifiche E04 e file
  di piano/handoff non ancora committati.
- [ ] E02 non è archiviato sotto `openspec/changes/archive/`; prima dell’archive
  occorre decidere se il push pubblico è autorizzato o esplicitamente rinunciato,
  poi seguire sync/archive secondo le skill OpenSpec.
- [ ] E03 sync wizard è ancora fuori archive (`openspec/PLAN.md` § E03).
- [ ] E04 ha task completi ma resta `in-progress` nel piano; occorre review,
  sync/archive e aggiornamento della voce solo dopo verifica del contratto.
- [ ] Neo4j non ha ancora una voce di piano canonica. Non reintrodurre gli artifact
  Neo4j del vecchio checkpoint senza una nuova decisione/approvazione.

## 💡 What Worked

La sequenza router → apply → verify ha impedito di lavorare sulla copia sbagliata:
la verifica iniziale nella directory installata ha rivelato la divergenza, la
sostituzione tramite symlink ha ristabilito il repo canonico, e la verifica E02 è
stata ripetuta direttamente lì. Il router ha selezionato la fase 6-apply perché
il change Docker aveva task aperti; `openspec-apply-change` è stato usato solo fino
al confine autorizzato.

Il lock file-per-file e il worktree check hanno preservato il lavoro E04 parallelo.
La suite completa, invece di soli test Docker, è stata decisiva: le modifiche E04
sono rimaste verdi insieme ai requisiti Docker/WhatsApp.

La documentazione staged è più operativa della precedente: il QR viene collegato
prima, l’UUID viene recuperato dall’API autenticata e il watchdog parte solo dopo
la configurazione. `AGENT_REGISTRY_DATA_SOURCE` mantiene il default isolato e
consente il bind host senza cambiare il compose pubblico (`README.md` § Sandbox
Docker; `SKILL.md` § Avvio).

## ❌ What Didn’t Work

- La prima applicazione è stata fatta sulla copia installata
  `.codex/skills/agent-registry`, non sul repo Git canonico. Quelle modifiche non
  erano pubblicabili direttamente; la sorgente corretta è ora il repo
  `agent-registry-oss`.
- `openspec status` non accetta il nome storico numerico del Docker change; il
  limite è della CLI e non si risolve rinominando il change senza migrazione degli
  artifact.
- Il router/apply delegato non è partito nella seconda applicazione canonica e ha
  dovuto essere interrotto prima di qualsiasi scrittura. La chiusura è stata
  completata manualmente con lock, `apply_patch` e verifica ripetuta.
- Il git-sync del registry ha segnalato pull/rebase sospeso in presenza di
  modifiche unstaged. Non tentare stash/reset o commit globale per “sbloccarlo”:
  prima separare le modifiche e ottenere conferma.
- Il vecchio HANDOFF-002 indicava Neo4j come E02; dopo l’unificazione il piano
  canonico è diverso. In caso di conflitto vince sempre `openspec/PLAN.md` della
  sorgente canonica, non il vecchio handoff.

## 🚀 Next Steps

1. Eseguire il blocco `Stato volatile` e confermare che E02 abbia ancora un solo
   task aperto; verificare anche i lock e il worktree prima di qualsiasi azione.
2. Decidere esplicitamente se il task E02 6.4 (version bump, commit e push pubblico)
   è autorizzato. Se sì, isolare/stagiare solo i file E02 e non includere le
   modifiche E04 o i file di piano non pertinenti.
3. Se il push non è autorizzato, documentare la rinuncia nel piano secondo il
   contratto E02 e procedere con sync/archive senza inventare un commit pubblico.
4. Chiudere E03 con `openspec-sync-specs`/`openspec-archive-change` dopo aver
   verificato il delta wizard; aggiornare la voce E03 soltanto quando l’archive è
   reale.
5. Completare review e chiusura E04 `agent-authored-notifications`, mantenendo il
   vincolo: firma dinamica del provider, nessuna API LLM dal watchdog e fallback
   per eventi non componibili (`openspec/PLAN.md` § E04).
6. Per Neo4j, avviare `plan-mode` con una proposta separata: definire obiettivo,
   dipendenza da E02, Community self-hosted, Markdown come fonte autorevole,
   embedding locali, rebuild idempotente, query semantiche/grafo e demo/test;
   fermarsi per approvazione prima di creare il change.

## 📎 Context Importante

- Sorgente canonica: `/Users/giuseppedicanosa/Claude-Projects/agent-registry-oss`.
  Le copie installate sono derivate; verificare sempre `readlink` prima di
  modificare una skill.
- Il repository è sul branch di lavoro Docker/WhatsApp con base pubblica già a
  v0.5.0; il numero esatto e i commit vanno rigenerati da `git status`/`git log`,
  non ereditati dal testo di HANDOFF-003.
- Configurazioni sensibili restano in `.env` e non devono apparire nell’handoff:
  API key, numero destinatario, UUID open-wa, token e credenziali SSH non vanno
  copiati qui.
- Non dichiarare il goal completo finché il piano conserva change attivi fuori
  archive, il task E02 6.4 non è deciso e E04 non è chiuso.
