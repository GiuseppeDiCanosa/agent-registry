# agent-registry

Coordinamento fra agenti AI CLI (Claude, Kimi, Gemini, Codex…) che lavorano
**contemporaneamente sullo stesso progetto**, senza pestarsi i piedi.

Mantiene un registry condiviso — chi sta lavorando, su cosa, quali file sono
bloccati, quali handoff esistono — e lock su file che reggono davvero fra
processi indipendenti.

```bash
npx tessl i spec-driven-development/agent-registry
```

## Il problema

Fai girare Claude e Kimi sullo stesso repo. Entrambi decidono di rifattorizzare
`src/auth.py`. Il secondo sovrascrive il lavoro del primo, e nessuno dei due se
ne accorge finché non è tardi.

## Come funziona

```bash
# 1. Registrati
python "$SKILL_DIR/registry_manager.py" register \
  "kimi-$(date +%s)" "Kimi" "2.7" "Refactoring auth" "src/auth.py" "analisi"

# 2. Acquisisci il lock prima di modificare — l'exit code è il contratto
if python "$SKILL_DIR/lock_manager.py" acquire "src/auth.py" "$SID"; then
    echo "lock ottenuto, procedo"
else
    echo "occupato da un altro agente, non tocco il file"
fi

# 3. A fine sessione (rilascia anche i lock)
python "$SKILL_DIR/registry_manager.py" finish "$SID"
```

Il registry porta con sé un **blocco di protocollo** che istruisce qualunque
agente lo apra: gli agenti dei vari provider non condividono alcun sistema di
skill, quindi le regole viaggiano con lo stato che descrivono, non con la skill
di un singolo CLI.

C'è anche una dashboard FastAPI (`scripts/webapp/`) per vedere lo stato in
tempo reale su `localhost:8765`.

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

- Il default `~/Desktop/agent-registry/` è mono-macchina e mono-progetto, ed è
  spesso sincronizzato da iCloud (che può creare copie in conflitto). Usa
  `AGENT_REGISTRY_PATH` e `AGENT_REGISTRY_LOCK_DIR` per spostarlo su un
  filesystem locale, fuori dalle cartelle sincronizzate.
- Filesystem di rete (NFS) non sono supportati: le garanzie di `flock` lì sono
  deboli o assenti.
- La granularità del lock è il file, non il simbolo.

## Licenza

MIT — vedi [LICENSE](LICENSE).
