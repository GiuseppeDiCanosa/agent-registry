# Tasks: agent-registry-sync-setup-wizard

> Lavoro nel repo standalone `~/Agent-Registry/repo`; al termine sincronizzare la copia in `.agents/skills/agent-registry/`. Test: `python3 -m pytest tests/ -q` dalla root del repo standalone.

## 1. Pre-validazione remote (D1)

- [x] 1.1 In `scripts/sync_manager.py`: funzione `_classify_lsremote_error(stderr, returncode)` pura che classifica in `malformed_url | auth_failed | unreachable | unknown` (pattern multipli su stderr, fallback unknown con stderr allegato).
- [x] 1.2 Funzione `validate_remote(url) -> dict` (`{ok, state: "empty"|"populated", error_kind?, message?}`) che esegue `git ls-remote` con `GIT_TERMINAL_PROMPT=0` e timeout 30s, usando `_classify_lsremote_error`.
- [x] 1.3 Test unitari di `_classify_lsremote_error` su stderr realistici (SSH denied, HTTPS auth, host irrisolvibile, timeout, URL malformato).

## 2. Setup a tre rami (D2, D7)

- [x] 2.1 Funzione `_home_has_user_data(home)` — True se esistono `sessions/*.yaml`, `wiki/*.md` o `contexts/*` con contenuto.
- [x] 2.2 Funzione `setup_git_sync(url, home=None, confirm_public=False, confirm_merge=False) -> dict` con i tre rami: (a) `init`+push (logica esistente), (b) `clone` in tmp + spostamento `.git` (solo se `_home_has_user_data` è False, altrimenti ramo c), (c) `integrazione`: add remote + fetch + `pull --rebase` sul branch di default del remote, con `--allow-unrelated-histories` se serve + `_resolve_conflict` esistente. Rami (b) e (c) operano sul branch di default del remote (non hardcoded `main`). Ritorna `{status, branch: "init"|"clone"|"integrazione", message}`.
- [x] 2.3 `init_git_sync` delega a `setup_git_sync` (contratto CLI `init --git-remote` invariato); nel caso integrazione la CLI richiede conferma interattiva (o flag esplicito) prima di procedere.
- [x] 2.4 Identità git con hostname: `_ensure_git_identity` usa `agent-registry@<socket.gethostname()>` per repo nuovi (D7); repo esistenti non toccati.
- [x] 2.5 Test ramo (a) con remote bare `file://` vuoto in `tmp_path`: init + push riusciti.
- [x] 2.6 Test ramo (b): remote bare popolato + home senza `.git` senza dati utente → clone, sessioni remote presenti nella home; includere caso branch di default diverso da `main`.
- [x] 2.7 Test ramo (b)→(c) guard: home con dati utente + remote popolato → integrazione, nessun `reset --hard`, dati locali preservati.
- [x] 2.8 Test ramo (c): home git con commit locali + remote popolato (history non correlata) → integrazione riuscita, vista rigenerata, sessioni locali e remote entrambe presenti.
- [x] 2.9 Guard "già configurato": `setup_git_sync` su home con sync già attivo → `{status: "ok", branch: None, message: "già configurato"}` senza modifiche; test dedicato.
- [x] 2.10 Conferma integrazione obbligatoria: ramo (c) senza `confirm_merge` → `{status: "needs_confirm", reason: "merge_with_local_data"}` senza side-effect; test dedicato (CLI inclusa).
- [x] 2.11 Fallback TOCTOU: push iniziale rifiutato perché il remote è stato popolato nel frattempo → ricaduta sul ramo integrazione (con conferma), mai push forzato; test con remote bare popolato dopo la validazione.

## 3. Verifica repo pubblico (D3)

- [x] 3.1 Funzione `check_github_visibility(url, token) -> "private"|"public"|"unknown"` via GitHub API (urllib stdlib), solo per host github.com e solo se token presente.
- [x] 3.2 Integrazione in `setup_git_sync`: `public` senza `confirm_public` → `{status: "needs_confirm", reason: "public_repo"}` senza side-effect; con conferma → procede.
- [x] 3.3 Test con chiamata HTTP mockata (callable iniettata o monkeypatch urllib): public/private/unknown.

## 4. Endpoint dashboard (D4)

- [x] 4.1 `POST /api/sync/init` in `scripts/webapp/main.py`: body `{url, confirm_public?, confirm_merge?}`, risposta `{status, branch?, message, detail?}`; nessuno side-effect su `needs_confirm`/`error`.
- [x] 4.2 `GET /api/sync` già espone `enabled`: verificare che basti alla UI (altrimenti estendere).
- [x] 4.3 Test endpoint con TestClient: ok (ramo init con remote bare), error auth, needs_confirm public, idempotenza chiamata ripetuta con conferma.

## 5. Setup card UI (D5)

- [x] 5.1 In `static/index.html`: setup card in cima visibile quando `GET /api/sync` → `enabled: false` (campo URL, bottone "Configura multi-macchina", area messaggi esito/errore, checkbox conferma per repo pubblico quando richiesta).
- [x] 5.2 Al successo: card nascosta, stato sync normale visibile; aggiornamento via refresh stato esistente (SSE/polling).
- [ ] 5.3 Verifica manuale in browser: card presente senza sync, setup riuscito end-to-end con remote bare locale, card assente dopo.

## 6. Trigger agente in SKILL.md (D6)

- [x] 6.1 Aggiornare `SKILL.md` (flusso obbligatorio, dopo `status`): proposta setup se `sync enabled: false`, una sola volta per sessione; avvio dashboard in background + `open http://localhost:<porta>`; gestione porta occupata (riuso se è la dashboard del registry, altrimenti porta libera).
- [x] 6.2 Mantenere SKILL.md sotto i 5000 token (verifica con stima chars/4).

## 7. Chiusura

- [ ] 7.1 Suite completa verde nel repo standalone (`python3 -m pytest tests/ -q`).

## Passi manuali post-loop (fuori dal loop, eseguiti dall'operatore)

1. Sincronizzare la copia in `.agents/skills/agent-registry/` (SKILL.md + scripts + webapp + tests).
2. Suite verde anche dalla copia QuokoWeb.
3. Commit repo standalone (release 0.3.1) e push su GitHub.
4. Pubblicazione tessl `agent-registry@0.3.1`.
5. Commit in QuokoWeb: skill sincronizzata + artifacts openspec del change.
