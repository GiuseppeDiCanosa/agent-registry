---
version: "1.0"
last_updated: ""
agents: []
---

<!-- PROTOCOL:START — rigenerato a ogni scrittura, non modificare a mano -->
# AGENT REGISTRY — PROTOCOLLO OBBLIGATORIO

> **Se stai leggendo questo file come agente AI, queste regole valgono per te.**
>
> 1. **Leggi prima di toccare.** Non modificare alcun file elencato nella
>    colonna `Do Not Touch` di un agente con status `OnWorking`: c'è un altro
>    agente che ci sta lavorando in questo momento.
> 2. **Registrati prima di lavorare:**
>    `python <skill>/scripts/registry_manager.py register <session_id> <provider> <versione> "<cosa stai facendo>" "<file,toccati>" "<todo,correnti>"`
> 3. **Acquisisci il lock prima di ogni modifica:**
>    `python <skill>/scripts/lock_manager.py acquire <path> <session_id>`
>    Exit code 0 = il lock è tuo; diverso da 0 = è di un altro, **fermati**.
> 4. **Tieni vivo il lock** se il lavoro supera i 120s: `heartbeat-loop`.
> 5. **A fine sessione:** `registry_manager.py finish <session_id>`, che
>    rilascia anche i lock della sessione.
>
> **I lock sono advisory.** Nessuno impedisce fisicamente la scrittura su un
> file bloccato: la protezione funziona solo se ogni agente rispetta questo
> protocollo. Se lo ignori, il coordinamento salta per tutti.
<!-- PROTOCOL:END -->

| Session ID | Provider | AI Version | Started At (Rome) | Working On | To Do Past | To Do Present | To Do Future | Space | Do Not Touch | Status | Issues | Handoff |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
