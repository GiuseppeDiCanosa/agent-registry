# Tasks — Sandbox Docker + notifiche WhatsApp

## 1. Spec
- [x] 1.1 Capability `container-deployment` + `whatsapp-notifications` (canoniche + delta change)

## 2. Immagine & orchestrazione
- [x] 2.1 `docker/Dockerfile` (python:3.13-slim, git, deps webapp+scripts) + header GENERATED
- [x] 2.2 `.dockerignore`
- [x] 2.3 `docker-compose.yml` (5 servizi, volume `/data`, `AGENT_REGISTRY_HOME`, domini OrbStack)
- [x] 2.4 `docker/sync-loop.sh` (init + loop sync, errori non fatali)
- [x] 2.5 `docker compose config` valido; 5 servizi rilevati

## 3. Notifier
- [x] 3.1 `notifier/watchdog.py` (classify_events, render_message, main)
- [x] 3.2 `notifier/wa_client.py` (build_send_request + send_text)
- [x] 3.3 `notifier/messages.default.json` (pool clean templato, pubblico)
- [x] 3.4 `notifier/messages.local.json` (150 sfottò, generati da subagente Sonnet 5, gitignored)

## 4. Test & segreti
- [x] 4.1 `tests/docker/test_compose.py` (8 test) + `tests/notifier/test_watchdog.py` (6 test) — verdi
- [x] 4.2 `.env.example` + `.gitignore` aggiornato (.env, messages.local.json, wiki.db, ...)
- [x] 4.3 Suite completa verde (209 passed)

## 5. Verifica reale (OrbStack)
- [x] 5.1 `docker compose build` (immagine agent-registry:local) — OK
- [x] 5.2 `docker compose up -d db dashboard code` → dashboard vede sessioni dal volume — OK
- [x] 5.3 `wa-gateway`: immagine `ghcr.io/rmyndharis/openwa:latest`, QR scansionato, sessione `ready` — OK
- [x] 5.4 Payload send-text confermato (`chatId`@c.us + `text`, header X-API-Key). **Nota: sessionId nel path = UUID, non il nome.**
- [x] 5.5 Evento `executed` → messaggio WhatsApp **consegnato** ✅ (stopped/idle stessa strada). wa_client logga il corpo errore.

## 6. Chiusura
- [ ] 6.1 `verify.sh` (spec-verify) verde
- [ ] 6.2 `work-review` requisito-per-requisito
- [ ] 6.3 README/SKILL: sezione avvio via Docker
- [ ] 6.4 Commit + **push pubblico** (bump v0.4.0) — solo dopo conferma esplicita utente
