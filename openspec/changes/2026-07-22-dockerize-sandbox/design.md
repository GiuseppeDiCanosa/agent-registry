# Design — Sandbox Docker + notifiche WhatsApp

## D1 — Immagine unica, comandi diversi
Un solo `docker/Dockerfile` (`python:3.13-slim` + git/curl) installa `scripts/` e tutte le
deps (`scripts/webapp/requirements.txt` + `scripts/requirements.txt`). I servizi `db`,
`dashboard`, `code`, `watchdog` usano la stessa immagine `agent-registry:local` con `command`
diversi. `wa-gateway` usa l'immagine esterna di open-wa. Uvicorn ascolta su `0.0.0.0` per
essere raggiungibile via dominio OrbStack.

## D2 — Persistenza: volume condiviso, `AGENT_REGISTRY_HOME=/data`
Named volume `agent-registry-data` montato su `/data` in db/dashboard/code/watchdog; mai
`AGENT_REGISTRY_PATH`. Default = sandbox isolata dall'host. Alternativa documentata (non
default): bind-mount `~/.agent-registry:/data` per condividere con gli agenti host.

## D3 — Domini OrbStack
Label `dev.orbstack.domains` per ogni servizio: `<servizio>.agent-registry.orb.local` (il
gateway usa `wa.`). Nessun reverse proxy esterno.

## D4 — Dashboard sempre accesa
`restart: unless-stopped` su tutti i servizi; healthcheck HTTP su `/api/sync`. Con OrbStack
attivo al login il compose riparte dopo un riavvio.

## D5 — Segreti fuori dall'immagine
`.env` gitignored + `.env.example` con placeholder. `KIMI_API_KEY` → `code`; `WA_*` →
watchdog/gateway; auth git del `db` = mount read-only di `~/.gitconfig` e `~/.ssh`. Nessun
segreto nel Dockerfile o nel repo.

## D6 — Watchdog: eventi da transizioni di stato
`classify_events` (pura, testata) confronta lo stato corrente col precedente e emette
`executed` (→Finished), `stopped` (→Stop/Killed), `idle` (OnWorking senza attività da
>soglia, default 3600s), ognuno una sola volta. Lo stato è persistito in
`/data/.watchdog-state.json`. `last_activity` = mtime del file sessione (proxy dell'ultimo
aggiornamento/heartbeat). Messaggi scelti a caso dal pool, placeholder sostituiti in modo
robusto (niente `str.format`, tollera graffe rogue).

## Rischi
- `fcntl` regge solo su volume locale condiviso (no NFS).
- Il payload/endpoint esatto di open-wa (`to`/`text`) va confermato con le docs reali del
  repo OpenWA al primo test col gateway.
- `last_activity` da mtime è un proxy: se un heartbeat aggiorna solo il lock e non il file
  sessione, l'idle potrebbe scattare prima; da rifinire dopo verifica reale.
