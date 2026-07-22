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

## 5. Verifica reale (OrbStack) — DA FARE al risveglio
- [ ] 5.1 `docker compose build` (immagine agent-registry:local)
- [ ] 5.2 `docker compose up -d db dashboard code` → aprire `dashboard.agent-registry.orb.local`
- [ ] 5.3 `wa-gateway`: finalizzare immagine reale OpenWA, `docker compose up wa-gateway`, **scan QR**
- [ ] 5.4 Confermare payload send-text reale di open-wa (`to`/`text`) e correggere `wa_client` se serve
- [ ] 5.5 Simulare i 3 eventi e verificare l'arrivo del messaggio su WhatsApp

## 6. Chiusura
- [ ] 6.1 `verify.sh` (spec-verify) verde
- [ ] 6.2 `work-review` requisito-per-requisito
- [ ] 6.3 README/SKILL: sezione avvio via Docker
- [ ] 6.4 Commit + **push pubblico** (bump v0.4.0) — solo dopo conferma esplicita utente
