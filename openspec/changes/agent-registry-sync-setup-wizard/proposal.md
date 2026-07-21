# Proposal: agent-registry-sync-setup-wizard

## Why

Il git-sync multi-macchina della skill `agent-registry` (v0.3.0) funziona, ma il setup è un comando CLI (`sync_manager.py init --git-remote <url>`) che nessuno ricorda: di fatto il multi-macchina resta inutilizzato. Inoltre `init` gestisce solo il caso "prima macchina, remote vuoto": sulla seconda macchina (remote già popolato) produrrebbe history non correlate e conflitti. Serve un setup guidato, proposto automaticamente al primo avvio e completabile dalla dashboard con un semplice campo URL, che distingua e gestisca correttamente tutti i casi (prima macchina, seconda macchina, dati locali preesistenti) e gli errori di autenticazione.

## What Changes

- **Pre-validazione del remote**: prima di qualsiasi side-effect, il sistema verifica il remote con `git ls-remote` e classifica il caso (URL malformato, auth fallita, remote vuoto, remote popolato).
- **Setup a tre rami** in base allo stato di home e remote:
  - remote vuoto + home senza dati remoti → flusso attuale (init + primo commit + push);
  - remote popolato + home senza `.git` (seconda macchina) → clone del remote nella home;
  - remote popolato + home git con dati locali → set remote + `pull --rebase` con risoluzione conflitti tramite rigenerazione della vista dai file per-sessione.
- **Wizard in dashboard**: quando il git-sync non è configurato, la dashboard mostra una setup card con campo URL remote; un nuovo endpoint `POST /api/sync/init` esegue validazione + setup e riporta esiti ed errori in UI.
- **Proposta automatica al primo avvio**: l'agente che legge il registry e trova il sync non configurato propone il setup all'utente; se accettato, avvia la dashboard (gestendo porta occupata) e apre il browser.
- **Warning repo pubblico**: se il remote è un repository GitHub pubblico, il sistema avvisa che il registry contiene contesto di lavoro potenzialmente sensibile e chiede conferma esplicita.
- **Errori di autenticazione espliciti**: fallimenti SSH/HTTPS vengono rilevati in validazione e riportati con indicazioni risolutive, invece di fallire silenziosamente al primo push.
- **Identità git per macchina**: i commit di auto-sync includono l'hostname, per distinguere le macchine nella history.

## Capabilities

### New Capabilities

(nessuna — il change estende capability esistenti)

### Modified Capabilities

- `agent-registry-storage`: nuovi requisiti per pre-validazione del remote e setup guidato a tre rami (init / clone / merge), errori di auth espliciti, warning repo pubblico, identità git con hostname.
- `agent-registry-dashboard`: nuovo requisito per la setup card multi-macchina e l'endpoint di inizializzazione sync con report di esito in UI.
- `agent-registry`: nuovo requisito per la proposta automatica del setup quando l'agente rileva il sync non configurato alla lettura del registry.

## Impact

- **Codice skill** (`~/Agent-Registry/repo`, poi sincronizzato in `.agents/skills/agent-registry/`):
  - `scripts/sync_manager.py`: funzioni di pre-validazione (`ls-remote`, classificazione caso), rami clone/merge, identità git con hostname.
  - `scripts/webapp/main.py`: endpoint `POST /api/sync/init` (+ eventuale estensione di `GET /api/sync` con stato "non configurato").
  - `scripts/webapp/static/index.html`: setup card con form URL, messaggi di errore/conferma.
  - `SKILL.md`: istruzione di proposta setup al primo avvio (nel flusso di sessione obbligatorio).
- **Test**: nuovi test pytest per validazione, tre rami di setup, endpoint dashboard (con git reale su remote locale `file://` o mock).
- **Dati runtime**: nessuna migrazione; home esistenti con sync già configurato non sono toccate.
- **Compatibilità**: nessun breaking change — `sync_manager.py init --git-remote` resta funzionante e instrada attraverso la stessa logica di validazione.
