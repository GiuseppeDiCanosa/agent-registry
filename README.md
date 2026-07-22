# agent-registry

Coordinamento fra agenti AI CLI (Claude, Kimi, Gemini, Codex…) che lavorano
**contemporaneamente sullo stesso progetto**, senza pestarsi i piedi.

Mantiene un registry condiviso — chi sta lavorando, su cosa, quali file sono
bloccati, quali handoff esistono — e lock su file che reggono davvero fra
processi indipendenti.

```bash
npx tessl i spec-driven-development/agent-registry
```

## Sandbox Docker (OrbStack)

Ambiente containerizzato riproducibile: dashboard sempre accesa e raggiungibile
per nome, persistenza su volume, notifiche WhatsApp sugli eventi degli agenti.
Cinque servizi (`docker-compose.yml`): **db** (persistenza `/data` + git-sync),
**dashboard** (webapp :8765, sempre accesa), **code** (sandbox runtime per gli
agenti), **wa-gateway** (WhatsApp via [open-wa](https://github.com/rmyndharis/OpenWA)),
**watchdog** (notifica `started` / `executed` / `stopped` / `idle >1h`).

```bash
cp .env.example .env      # compila WA_RECIPIENT, WA_NAME, chiavi… (mai committare .env)
docker compose build
docker compose up -d db dashboard code   # sandbox base
open http://dashboard.agent-registry.orb.local   # o http://localhost:8765
```

Per le notifiche WhatsApp, avvia il gateway e collega l'account **scansionando il
QR** una volta (necessario, richiede il telefono):

```bash
docker compose up -d wa-gateway watchdog
# apri il gateway e scansiona il QR con WhatsApp del telefono
```

Il servizio **db** sincronizza la home verso il remote git via **SSH**: l'immagine
include `openssh-client` e il container monta in sola lettura la chiave e la config git
dell'operatore (`~/.ssh`, `~/.gitconfig`), così l'auth avviene senza segreti dentro
l'immagine (usa quindi un remote `git@github.com:…`, non HTTPS).

I segreti (numero, API key, credenziali git) stanno solo in `.env` (gitignored).
Il pool pubblico dei messaggi è `notifier/messages.default.json` (templato,
neutro); un pool personale può essere messo in `notifier/messages.local.json`
(gitignored), che ha la precedenza. La home `/data` è un volume isolato: per
condividere il registry con gli agenti dell'host, sostituisci il named volume con
un bind-mount `~/.agent-registry:/data` in `docker-compose.yml`.

## Cosa è cambiato nella 0.5.0

La 0.5.0 porta il registry **fuori dal singolo terminale**: un ambiente containerizzato
riproducibile e le notifiche degli agenti su WhatsApp.

- **Sandbox Docker (OrbStack).** Cinque servizi orchestrati via `docker-compose.yml`
  (`db`, `dashboard`, `code`, `wa-gateway`, `watchdog`): dashboard sempre accesa e
  raggiungibile per nome, persistenza su volume, un'unica immagine autosufficiente
  (Python 3.13 + `git` + `openssh-client` + dipendenze).
- **Notifiche WhatsApp sugli eventi degli agenti.** Il `watchdog` osserva lo stato e
  notifica quattro eventi — **`started`** (una sessione entra in `OnWorking`, inclusa la
  prima comparsa), `executed` (→ `Finished`), `stopped` (→ `Stop`/`Killed`), `idle`
  (`OnWorking` fermo oltre soglia). Ogni evento è emesso una sola volta, con avvio a
  freddo che semina lo stato senza notifiche storiche. Messaggi da pool templato
  (`notifier/messages.default.json`), con pool personale `messages.local.json`
  (gitignored) prioritario. Invio via gateway [open-wa](https://github.com/rmyndharis/OpenWA),
  nessun numero/API key nel codice.
- **Git-sync multi-macchina via SSH.** Il servizio `db` sincronizza la home verso il
  remote in loop, autenticandosi via chiave SSH montata in sola lettura — nessuna
  credenziale incorporata nell'immagine.

## Cosa è cambiato nella 0.3.0

La 0.2.x coordinava gli agenti **nel presente**; la 0.3.0 gli dà anche una
**memoria** e un modo per seguirli **fra macchine diverse**.

- **Wiki — memoria collettiva delle sessioni.** A fine lavoro
  `registry_manager.py end <sid>` distilla il context della sessione in un
  wiki entry (cosa, come, problema risolto, bug, file toccati). Prima di
  iniziare un lavoro, `wiki query "<domanda>"` risponde se qualcosa di simile
  è già stato fatto — il router è generato via LangChain/Kimi
  (`wiki ingest`, `ingest-pending` per il recupero dei pending).
- **Git-sync multi-macchina.** La home del registry può essere un clone di un
  repo GitHub **privato**: ogni scrittura schedula un commit+push in
  background (`sync_manager`), così gli agenti su macchine diverse vedono le
  sessioni altrui. Best-effort totale: un sync fallito non blocca mai il
  registry.
- **Storage per-sessione in `~/.agent-registry/`.** Ogni sessione vive in
  `sessions/<id>.yaml` (fonte di verità); `registry.md` resta una vista
  rigenerata. Niente più default su Desktop sincronizzato da iCloud; la home
  si sposta con `AGENT_REGISTRY_HOME`.
- **Dashboard operativa** (`scripts/webapp/`, `localhost:8765`): filtri per
  status/provider/progetto, kill di una sessione (SIGTERM reale con verifica
  anti-riuso PID, o stop logico cross-macchina) e cleanup degli zombie.
- **CLI completa, exit code come contratto.** `register`, `update`, `finish`,
  `end`, `kill`, `cleanup`, `status`, `context log`, `wiki …`: ogni comando
  esce non-zero se l'operazione fallisce. Batch check su più path in una
  chiamata (`lock_manager.py check a.py b.py c.py`) e `cleanup` che marca
  Stop le sessioni zombie rilasciandone i lock residui.

## Il problema

Fai girare Claude e Kimi sullo stesso repo. Entrambi decidono di rifattorizzare
`src/auth.py`. Il secondo sovrascrive il lavoro del primo, e nessuno dei due se
ne accorge finché non è tardi.

## Come funziona

```bash
# 1. Registrati
python "$SKILL_DIR/registry_manager.py" register \
  "kimi-$(date +%s)" "Kimi" "2.7" "Refactoring auth" \
  --space "src/auth.py" --todo-present "analisi"

# 2. Acquisisci il lock prima di modificare — l'exit code è il contratto
if python "$SKILL_DIR/lock_manager.py" acquire "src/auth.py" "$SID"; then
    echo "lock ottenuto, procedo"
else
    echo "occupato da un altro agente, non tocco il file"
fi

# 3. A fine sessione: distilla in wiki entry e rilascia i lock
python "$SKILL_DIR/registry_manager.py" end "$SID" \
  --cosa "refactoring auth" --come "estratto modulo" --risolto "duplicazione"
# (oppure solo chiusura: registry_manager.py finish "$SID")
```

Il registry porta con sé un **blocco di protocollo** che istruisce qualunque
agente lo apra: gli agenti dei vari provider non condividono alcun sistema di
skill, quindi le regole viaggiano con lo stato che descrivono, non con la skill
di un singolo CLI.

C'è anche una dashboard FastAPI operativa (`scripts/webapp/`) su
`localhost:8765`: stato in tempo reale, filtri per status/provider/progetto,
kill delle sessioni e cleanup degli zombie.

## I lock sono advisory — leggilo prima di fidarti

Il lock **non impedisce fisicamente** la scrittura: protegge solo gli agenti che
lo consultano. Se un agente ignora il protocollo, sovrascriverà comunque il
lavoro altrui e nessuno lo fermerà. Il valore sta interamente nel fatto che
*ogni* agente rispetti il protocollo.

## Cosa è cambiato nella 0.2.0

La 0.1.0 aveva un difetto che ne annullava lo scopo: **i lock non bloccavano
nulla** e il registry perdeva le registrazioni concorrenti.

```
# 0.1.0
A acquisisce auth.py     → {'locked': True, 'owner': 'claude-111'}
B ci prova 2s dopo       → {'locked': True, 'owner': 'kimi-222'}   ← lock rubato
8 agenti in parallelo    → 1 sopravvive su 8

# 0.2.0
A acquisisce auth.py     → {'locked': True,  'owner': 'claude-111'}      exit=0
B ci prova 2s dopo       → {'locked': False, 'session_id': 'claude-111'} exit=1
8 agenti in parallelo    → 8 su 8
```

La causa: `fcntl.flock` viene rilasciato dal kernel all'uscita del processo.
Siccome gli agenti lanciano comandi one-shot che terminano subito, il lock
viveva pochi millisecondi e il comando successivo trovava campo libero.

La correzione separa due cose che la 0.1.0 confondeva:

- **lo stato** (chi possiede il path) sta nel *contenuto di un file*, che
  sopravvive alla morte del processo;
- **la mutua esclusione** durante l'aggiornamento è data da `flock`, tenuto solo
  per la sezione critica dentro un processo vivo.

Stessa syscall, ruolo opposto. Il registry segue lo stesso principio: il lock
sta su `registry.md.lock`, file dedicato e **mai rinominato**, perché era la
coincidenza fra "file lockato" e "file sostituito via rename" a rendere inutile
il lock precedente.

Altri fix: `finish` rilascia i lock (registry e `locks/` non divergono più),
exit code significativi in entrambe le CLI, identità del lock su `realpath`,
escaping corretto nella tabella, path della doc risolti a runtime.

## Sviluppo

Il progetto segue **spec-as-source**: la spec è la sorgente, il codice il derivato.
Ogni requisito dichiara il test che lo verifica, e ogni file generato dichiara la
spec che lo governa nel proprio header.

Le spec **non viaggiano nel pacchetto pubblicato** — chi installa la skill vuole
il codice. Vivono nel repo, dove CI ed enforcement le usano davvero:
[`openspec/specs/`](https://github.com/GiuseppeDiCanosa/agent-registry/tree/main/openspec/specs).
È lì che punta il `Source:` negli header dei sorgenti.

```bash
pip install -r scripts/requirements.txt -r requirements-dev.txt
bash scripts/verify.sh   # link [@test] + ownership dei target + suite
```

I test girano in **processi separati che terminano davvero**, perché è così che
gli agenti usano la skill. La suite 0.1.0 era verde su codice rotto proprio
perché testava in-process, dove la mutua esclusione era garantita da un dict in
memoria e mai dal filesystem.

## Limiti noti

- La home di default è `~/.agent-registry/` (mono-macchina finché non si
  abilita il git-sync). Usa `AGENT_REGISTRY_HOME` e `AGENT_REGISTRY_LOCK_DIR`
  per spostarla; tienila su un filesystem locale, fuori dalle cartelle
  sincronizzate (iCloud e simili possono creare copie in conflitto). Per la
  condivisione multi-macchina usa il git-sync su repo GitHub privato, non una
  cartella sincronizzata.
- Filesystem di rete (NFS) non sono supportati: le garanzie di `flock` lì sono
  deboli o assenti.
- La granularità del lock è il file, non il simbolo.

## Licenza

MIT — vedi [LICENSE](LICENSE).
