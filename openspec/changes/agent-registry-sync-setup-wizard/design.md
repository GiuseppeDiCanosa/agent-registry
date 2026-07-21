# Design: agent-registry-sync-setup-wizard

## Context

La skill `agent-registry` (v0.3.0, repo standalone `~/Agent-Registry/repo`, copia in `.agents/skills/agent-registry/`) ha il git-sync multi-macchina implementato in `scripts/sync_manager.py`: la home `~/.agent-registry/` è un repo git con auto-sync (`add → commit → pull --rebase → push`, debounce ~2s in thread daemon, best-effort totale). Il setup però è solo CLI (`init --git-remote <url>`) e copre un solo caso: home senza `.git` + remote vuoto. Sulla seconda macchina (remote popolato) il flusso attuale produrrebbe history non correlate. Gli errori di autenticazione emergono solo al primo push, silenziosamente (`GIT_TERMINAL_PROMPT=0`). La dashboard FastAPI (`scripts/webapp/main.py` + `static/index.html`) espone già `GET /api/sync` per lo stato del sync.

Vincoli: nessun hook reale "all'installazione della skill" (le skill sono markdown passivo) → il trigger automatico è instruction-based, eseguito dall'agente alla lettura del registry. La validazione deve usare solo git + stdlib (come il resto di `sync_manager.py`). I test non devono richiedere rete né GitHub: remote `file://` su repo bare locali.

## Goals / Non-Goals

**Goals:**
- Setup multi-macchina completabile da un utente non tecnico: incolla URL → fatto.
- Gestione corretta dei tre casi: prima macchina (init), seconda macchina (clone), home con dati locali + remote popolato (merge).
- Errori di validazione espliciti e azionabili prima di qualsiasi side-effect.
- Proposta automatica del setup da parte dell'agente al primo avvio senza sync.

**Non-Goals:**
- Hosting/provisioning del repository remoto (l'utente crea il repo privato sul suo provider).
- Gestione credenziali (SSH agent, token store): il wizard rileva e spiega, non configura.
- Sync continuo/watch daemon: resta il modello write-triggered esistente.
- Migrazione di home già configurate: il wizard appare solo se il sync è assente.

## Decisions

- **D1 — Pre-validazione con `git ls-remote` come unico gate.** Prima di toccare la home si esegue `git ls-remote <url>` (timeout 30s, `GIT_TERMINAL_PROMPT=0`) e si classifica l'esito: exit 0 + output vuoto → remote vuoto; exit 0 + refs → popolato; stderr con `Permission denied`/`Authentication failed`/`could not read Username` → auth fallita; `Could not resolve hostname`/`Failed to connect`/timeout → non raggiungibile; URL che non parsa come scp-like o `scheme://` → malformato. Rationale: un solo comando read-only copre tutti i casi senza dipendenze nuove.
- **D2 — Tre rami in `setup_git_sync(url, home)`**, nuova funzione che incapsula la scelta; `init_git_sync` resta come entry point CLI e delega. Ramo (a) remote vuoto → logica attuale di init + primo push. Ramo (b) remote popolato + home senza `.git` → `git clone <url> <home-tmp>` e spostamento del `.git` nella home (la home esiste già con struttura dati; clone diretto in dir non vuota non è possibile): clone in tmp, `mv tmp/.git home/.git`, `git reset --hard origin/<branch>` **solo se la home non contiene dati utente** (verifica: nessun `sessions/*.yaml`, `wiki/*.md`, `contexts/*`); se contiene dati utente si ricade sul ramo (c). Ramo (c) home git (o home con dati non vuota) + remote popolato → add remote, `fetch`, `pull --rebase` (con `allow-unrelated-histories` se necessario); su conflitto: abort, rigenera vista dai `sessions/*.yaml` (meccanismo `_resolve_conflict` esistente), retry. Rationale: i file per-sessione hanno un solo owner per costruzione, i conflitti reali sono rari e limitati alla vista rigenerabile.
- **D3 — Verifica repo pubblico solo per host GitHub, best-effort.** Se l'URL è github.com e `GITHUB_TOKEN`/`GH_TOKEN` è presente, `GET /repos/{owner}/{repo}` (urllib stdlib) → se `private: false` serve conferma esplicita (flag `confirm_public`). Se il token manca o l'host non è GitHub, il controllo è saltato con un avviso testuale nel risultato ("visibilità non verificata"). Rationale: non bloccare utenti non-GitHub, ma proteggere il caso più comune.
- **D4 — Endpoint `POST /api/sync/init` con corpo `{url, confirm_public?, confirm_merge?}` e risposta a stati.** La risposta è sempre `{status: "ok"|"needs_confirm"|"error", branch?: "init"|"clone"|"merge", message, detail?}`. `needs_confirm` non tocca nulla e indica cosa confermare; il client ripete la chiamata col flag di conferma. Rationale: setup idempotente e UI semplice (una sola chiamata ripetibile); gli stati parziali sono impossibili perché la validazione (D1) precede ogni side-effect.
- **D5 — Setup card in cima alla dashboard, pilotata da `GET /api/sync`.** Quando `enabled: false`, la card è mostrata sopra la tabella (campo URL + bottone + area messaggi); quando il setup va a buon fine la card scompare e appare lo stato sync normale. Rationale: la dashboard già polla/SSE lo stato sync — la card si aggiorna da sola senza navigazione.
- **D6 — Trigger agente instruction-based in SKILL.md.** Nel flusso obbligatorio, dopo `status`: "se `sync enabled: false`, proponi il setup multi-macchina all'utente (una sola volta per sessione); se accetta: avvia `uvicorn` in background dalla dir webapp (se la porta 8765 è occupata verifica con `GET /api/sync` che sia la dashboard del registry; altrimenti porta libera) e apri il browser con `open http://localhost:<porta>` (macOS) / equivalente". Rationale: è l'unico punto di aggancio realistico — la skill è passiva, ma la lettura del registry è già obbligatoria.
- **D7 — Identità git con hostname.** `_ensure_git_identity` imposta `user.email = agent-registry@<hostname>` (da `socket.gethostname()`) invece di `agent-registry@localhost`; i repo già inizializzati non vengono toccati. Rationale: history leggibile multi-macchina a costo zero.
- **D8 — Test con remote `file://` su repo bare in tmp.** I test dei tre rami usano `git init --bare` in `tmp_path` come remote: niente rete, niente mock del protocollo git, coprono clone/merge/push per davvero. Auth/URL malformato testati con URL finti e classificazione degli stderr (funzione pura `_classify_lsremote_error` testata unitaria).

## Risks / Trade-offs

- **Classificazione errori via string-matching su stderr git**: fragile rispetto a versioni/lingue di git. Mitigazione: pattern multipli, fallback "errore generico di accesso al remote" con stderr grezzo allegato.
- **Ramo clone con home pre-esistente**: lo spostamento di `.git` da tmp è un'operazione delicata; il guard "nessun dato utente" riduce il rischio, ma un giudizio errato farebbe `reset --hard` su dati utente. Mitigazione: il guard è conservativo — in dubbio ramo (c), mai cancellazione silenziosa.
- **Rebase con `allow-unrelated-histories`** nel ramo (c) può produrre conflitti su file derivati: accettato, perché la vista si rigenera e i dati per-sessione non confliggono in pratica.
- **Il wizard non risolve il setup delle credenziali**: l'utente deve comunque avere SSH/token funzionanti; il valore aggiunto è la diagnosi immediata invece del fallimento silenzioso al primo push.
- **Verifica repo pubblico dipendente da token**: senza token il controllo è saltato — compromesso accettato per non bloccare il flusso, con avviso esplicito.
