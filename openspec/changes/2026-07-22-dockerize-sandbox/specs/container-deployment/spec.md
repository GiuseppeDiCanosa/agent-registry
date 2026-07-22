---
targets:
  - docker/Dockerfile
  - docker-compose.yml
  - docker/sync-loop.sh
  - .dockerignore
---

# container-deployment Specification

## Purpose
Fornire un ambiente containerizzato, riproducibile e isolato in cui la skill agent-registry
gira come sandbox a più servizi orchestrati via Docker Compose e pensati per OrbStack. La
dashboard resta sempre accesa e raggiungibile per nome, la persistenza è un volume condiviso
esposto via `AGENT_REGISTRY_HOME`, e nessun segreto è incorporato nell'immagine o nel
repository. Il packaging non altera il comportamento degli script della skill.

## Requirements

### Requirement: Servizi che condividono la home del registry
Il compose SHALL definire i servizi `db`, `dashboard`, `code` e `watchdog` montando lo stesso
volume dati su `/data` e impostando `AGENT_REGISTRY_HOME=/data`. Nessun servizio SHALL usare
la variabile deprecata `AGENT_REGISTRY_PATH`.

**Verified by**: [@test] tests/docker/test_compose.py

#### Scenario: i servizi puntano tutti alla stessa home
- **WHEN** si esegue `docker compose config` sul file `docker-compose.yml`
- **THEN** esistono i servizi `db`, `dashboard`, `code`, `watchdog`
- **AND** ciascuno monta il medesimo volume su `/data` e ha `AGENT_REGISTRY_HOME=/data`
- **AND** nessun servizio definisce `AGENT_REGISTRY_PATH`

### Requirement: Home dati configurabile
La sorgente del volume `/data` SHALL essere configurabile tramite la variabile
`AGENT_REGISTRY_DATA_SOURCE`, con **default** un volume Docker isolato
(`agent-registry-data`). Impostandola a un percorso host, la sandbox opera sulla home reale,
così ogni agente che scrive in quella home viene osservato dal watchdog.

**Verified by**: [@test] tests/docker/test_compose.py

#### Scenario: sorgente /data parametrizzata con default isolato
- **WHEN** si ispeziona il mount `/data` dei servizi `db`, `dashboard`, `code`, `watchdog`
- **THEN** la sorgente usa `AGENT_REGISTRY_DATA_SOURCE` con default `agent-registry-data`

### Requirement: Dashboard sempre disponibile
Il servizio `dashboard` SHALL avviare la webapp su `0.0.0.0:8765`, avere `restart:
unless-stopped` e un healthcheck HTTP verso l'endpoint di stato.

**Verified by**: [@test] tests/docker/test_compose.py

#### Scenario: la dashboard è configurata come servizio persistente
- **WHEN** si ispeziona il servizio `dashboard` nella config compose
- **THEN** il comando avvia uvicorn su host `0.0.0.0` porta `8765`
- **AND** `restart` è `unless-stopped`
- **AND** è definito un healthcheck che interroga `/api/sync`

### Requirement: Dominio OrbStack per ogni servizio
Ogni servizio SHALL dichiarare il label `dev.orbstack.domains` con un dominio stabile
`<servizio>.agent-registry.orb.local`, così da essere raggiungibile per nome.

**Verified by**: [@test] tests/docker/test_compose.py

#### Scenario: ciascun servizio ha il suo dominio
- **WHEN** si ispezionano i label dei servizi
- **THEN** ogni servizio dichiara `dev.orbstack.domains`
- **AND** il valore contiene il nome del servizio come sottodominio di `agent-registry.orb.local`

### Requirement: Immagine unica autosufficiente
Il `Dockerfile` SHALL produrre un'unica immagine basata su Python 3.13 che contiene `git`,
gli script della skill e tutte le dipendenze (webapp + wiki-ingest), usata dai servizi
`db`, `dashboard`, `code` e `watchdog` con comandi diversi.

**Verified by**: [@test] tests/docker/test_compose.py

#### Scenario: i servizi Python condividono la stessa immagine
- **WHEN** si ispeziona la config compose
- **THEN** `db`, `dashboard`, `code` e `watchdog` fanno riferimento alla stessa build/immagine
- **AND** il `Dockerfile` installa sia `scripts/webapp/requirements.txt` sia
  `scripts/requirements.txt`

### Requirement: Nessun segreto nell'immagine o nel repository
L'immagine e il repository NON SHALL contenere segreti. Chiavi API, numero WhatsApp e
credenziali git SHALL essere forniti solo a runtime (env / `.env` gitignored / mount di sola
lettura). Il repository SHALL includere un `.env.example` con soli placeholder.

**Verified by**: [@test] tests/docker/test_compose.py

#### Scenario: i segreti arrivano da runtime, non dall'immagine
- **WHEN** si analizzano `Dockerfile`, `docker-compose.yml` e i file committati
- **THEN** non compaiono valori reali di chiavi, token o numeri di telefono in chiaro
- **AND** `.env` è elencato in `.gitignore`
- **AND** i segreti sono referenziati come variabili d'ambiente, non hardcoded

### Requirement: Il servizio db esegue il git-sync
Il servizio `db` SHALL eseguire periodicamente il git-sync della home verso il remote
configurato, restando attivo, senza che un fallimento di rete arresti il container.

**Verified by**: [@test] tests/docker/test_compose.py

#### Scenario: il db mantiene il ciclo di sync
- **WHEN** si ispeziona il comando/entrypoint del servizio `db`
- **THEN** invoca `sync_manager.py sync` in un loop a intervallo configurabile
- **AND** un errore di sync viene loggato ma non termina il processo
