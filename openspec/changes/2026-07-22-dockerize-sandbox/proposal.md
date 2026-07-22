# Proposal — Sandbox Docker (5 servizi) + notifiche WhatsApp

## Why

La skill dipende da Python e deps installati a mano: la dashboard non parte senza
`fastapi`/`uvicorn`, l'ingest wiki senza `langchain-openai`, e un avvio sbagliato con
`AGENT_REGISTRY_PATH` deprecata ha già causato una "dashboard vuota". Serve un ambiente
riproducibile e isolato in cui la dashboard giri sempre e sia raggiungibile per nome, più
un'automazione che avvisi l'operatore su WhatsApp quando un agente completa, si ferma o
resta inattivo troppo a lungo.

## What changes

Nuova orchestrazione Docker Compose (OrbStack) con cinque servizi:

- **db** — persistenza `/data` (=`AGENT_REGISTRY_HOME`) + git-sync della home.
- **dashboard** — webapp uvicorn su `0.0.0.0:8765`, sempre accesa, healthcheck `/api/sync`.
- **code** — sandbox runtime per gli agenti (register/acquire/end/wiki ingest).
- **wa-gateway** — gateway WhatsApp open-wa (immagine esterna, sessione via QR).
- **watchdog** — poller Python che rileva 3 eventi e invia una notifica WhatsApp.

Due capability nuove: `container-deployment` e `whatsapp-notifications`. Nessuna modifica al
comportamento degli script esistenti.

## Scope

**In**: `docker/Dockerfile`, `.dockerignore`, `docker-compose.yml`, `docker/sync-loop.sh`,
`notifier/watchdog.py`, `notifier/wa_client.py`, `notifier/messages.default.json`,
`.env.example`, i relativi test.

**Out**: migrazione a DB server (si resta file-based + SQLite); modifiche a
registry/lock/sync/webapp; esecuzione degli agenti host dentro il container (resta possibile
via bind-mount opzionale, documentato non default).

## Vincoli noti

- I lock sono `fcntl` advisory: validi fra container solo su volume condiviso sullo stesso
  host. Il coordinamento cross-macchina passa dal git-sync.
- Il gateway open-wa è automazione non ufficiale (rischio ban): usare un numero dedicato;
  richiede scan QR al primo avvio. Segreti e numero solo a runtime (`.env` gitignored).
- I 150 messaggi ironici personalizzati vivono in `notifier/messages.local.json` (gitignored);
  il repo pubblico spedisce il pool clean templato `messages.default.json`.
