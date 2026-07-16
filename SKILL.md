---
name: agent-registry
description: >
  Coordina multipli agenti AI CLI (Kimi, Claude, Gemini, OpenAI, ecc.) che lavorano
  contemporaneamente sullo stesso progetto. Usa questa skill SEMPRE quando lavori in
  parallelo con altre AI CLI, quando devi salvare lo stato di una sessione condivisa,
  quando vuoi evitare sovrascritture su file toccati da altri agenti, o quando l'utente
  parla di "multi-agente", "registry agenti", "coordination", "lock file", "handoff condiviso",
  "chi sta lavorando su cosa" o "evitare che le AI si pestino i piedi".
---

# Agent Registry — Coordination Multi-Agent

Permette a più agenti AI CLI di lavorare sullo stesso progetto senza sovrascriversi.
Mantiene un **registry condiviso** che dice chi sta lavorando, su cosa, quali file
sono bloccati e quali handoff esistono.

Il registry è un markdown leggibile da umani e da macchine, in
`~/Desktop/agent-registry/registry.md` (sovrascrivibile, vedi *Configurazione*).
Contiene in testa un **blocco di protocollo** che istruisce qualunque agente lo apra:
le regole viaggiano col file, così valgono anche per gli agenti che questa skill
non la caricano affatto.

---

## Regola d'oro

> **Leggi il registry prima di toccare qualsiasi file. Non modificare mai un file che è
> nel campo `do_not_touch` di un altro agente con status `OnWorking`.**

## I lock sono advisory — leggi qui prima di fidarti

Il lock **non impedisce fisicamente** la scrittura: non esiste alcun meccanismo che
blocchi un `write()`. Protegge solo gli agenti che lo consultano. Se un agente ignora
il protocollo, sovrascriverà comunque il lavoro altrui e nessuno lo fermerà.

Il valore della skill sta interamente nel fatto che **ogni** agente rispetti il
protocollo. Un agente che crede di essere protetto quando non lo è sta peggio di uno
che sa di non esserlo.

---

## Come invocare gli script

Gli script stanno in `scripts/` **dentro la directory di questa skill**, che cambia a
seconda di come è installata:

| Installazione | Percorso |
|---|---|
| `npx tessl i spec-driven-devlopment/agent-registry` | `.tessl/plugins/spec-driven-devlopment/agent-registry/scripts/` |
| Skill Claude Code locale | `.claude/skills/agent-registry/scripts/` |
| Copia manuale | dove l'hai messa |

**Non assumere il percorso: risolvilo una volta a inizio sessione** e riusalo.

```bash
SKILL_DIR=$(dirname $(find . ~/.claude/skills -name "lock_manager.py" -path "*agent-registry*" 2>/dev/null | head -1))
echo "$SKILL_DIR"   # es. ./.tessl/plugins/spec-driven-devlopment/agent-registry/scripts
```

Negli esempi che seguono `$SKILL_DIR` è quella directory.

**Dipendenze**: `pip install -r $SKILL_DIR/requirements.txt` (solo PyYAML).

---

## Primo passo all'avvio (obbligatorio)

1. Leggi `~/Desktop/agent-registry/registry.md` (o `$AGENT_REGISTRY_PATH`) e il suo
   blocco di protocollo.
2. Identifica i file `do_not_touch` degli agenti con status `OnWorking`.
3. Registra la tua sessione:

```bash
python "$SKILL_DIR/registry_manager.py" register \
  "kimi-$(date +%s)" "Kimi" "2.7" "Refactoring modulo auth" \
  "src/auth.py,src/oauth.py" "analisi codice,scrittura test"
```

Argomenti: `session_id`, `provider`, `ai_version`, `working_on`, `space` (file separati
da virgola), `todo_present`.

---

## Lock su file/aree

**Acquisisci il lock prima di ogni modifica.** L'esito sta nell'**exit code**: `0` = il
lock è tuo, diverso da `0` = è di un altro, fermati.

```bash
if python "$SKILL_DIR/lock_manager.py" acquire "src/auth.py" "kimi-1626451200"; then
    echo "lock ottenuto, procedo"
else
    echo "occupato da un altro agente, non tocco il file"
fi
```

Non fare parsing del testo stampato: l'exit code è il contratto.

### Heartbeat

I lock scadono dopo 120 secondi. Se il lavoro dura di più, rinnovali:

```bash
python "$SKILL_DIR/lock_manager.py" heartbeat "src/auth.py" "kimi-1626451200"
```

Oppure un loop in background (muore con la shell che lo ha lanciato):

```bash
python "$SKILL_DIR/lock_manager.py" heartbeat-loop "src/auth.py" "kimi-1626451200" 30 &
```

Solo l'owner può rinnovare o rilasciare un lock.

### Rilascio

```bash
python "$SKILL_DIR/lock_manager.py" release "src/auth.py" "kimi-1626451200"
```

---

## Aggiornare lo stato

```bash
python "$SKILL_DIR/registry_manager.py" update "kimi-1626451200" "Implementazione lock filesystem"
```

Per aggiornamenti granulari, da Python (con `scripts/` sul path):

```python
from registry_manager import update_session
update_session(
    "kimi-1626451200",
    working_on="Implementazione lock filesystem",
    todo={"past": ["setup"], "present": ["lock manager"], "future": ["webapp"]},
    space=["scripts/lock_manager.py"],
    do_not_touch=["src/auth.py"],
)
```

Dichiara in `do_not_touch` i file su cui hai preso il lock: è ciò che gli altri agenti
leggono, e ciò che `finish` userà per rilasciarli.

---

## Integrazione con handoff

```bash
python "$SKILL_DIR/registry_manager.py" handoff "kimi-1626451200" ".handoff-kimi/HANDOFF-007.md"
```

La convenzione è 1:1 — ogni sessione salva un handoff e ne registra il path.

---

## Fine sessione

```bash
python "$SKILL_DIR/registry_manager.py" finish "kimi-1626451200"
```

Marca la sessione `Finished`, svuota `do_not_touch` e **rilascia i lock della sessione**.
I lock di altri agenti non vengono toccati.

---

## Configurazione

| Variabile | Default | Uso |
|---|---|---|
| `AGENT_REGISTRY_PATH` | `~/Desktop/agent-registry/registry.md` | percorso del registry |
| `AGENT_REGISTRY_LOCK_DIR` | `~/Desktop/agent-registry/locks/` | directory dei lock |

**Metti registry e lock su un filesystem locale, fuori dalle cartelle sincronizzate.**
Il default sta su `~/Desktop`, che su molti Mac è sincronizzato da iCloud Drive: la
sincronizzazione può creare copie in conflitto proprio del file che deve essere la
fonte di verità. Anche i filesystem di rete (NFS) sono da evitare: le garanzie di
`flock` lì sono deboli o assenti, e il coordinamento salta in silenzio.

```bash
export AGENT_REGISTRY_PATH="$PWD/.agent-registry/registry.md"
export AGENT_REGISTRY_LOCK_DIR="$PWD/.agent-registry/locks"
```

---

## Web-app di monitoraggio

```bash
pip install -r "$SKILL_DIR/webapp/requirements.txt"
cd "$SKILL_DIR/webapp" && uvicorn main:app --port 8765
```

Poi apri http://localhost:8765 — la tabella si aggiorna ogni 5 secondi.

---

## Struttura della skill

```
agent-registry/
├── SKILL.md                     # questo file
├── templates/
│   └── registry-template.md     # registry vuoto + blocco di protocollo
└── scripts/
    ├── registry_manager.py      # lettura/scrittura registry
    ├── lock_manager.py          # lock filesystem + heartbeat
    ├── requirements.txt
    └── webapp/                  # dashboard FastAPI
```

---

## Note tecniche

- Il frontmatter YAML è l'unico dato autorevole; la tabella markdown è una vista
  rigenerata a ogni scrittura, come il blocco di protocollo.
- **Lo stato di un lock è il contenuto del suo file**, che sopravvive alla morte del
  processo: è ciò che rende il lock valido fra comandi CLI one-shot. `flock` serve solo
  a serializzare gli aggiornamenti, mai a rappresentare la proprietà.
- I lock file non vengono mai cancellati: il rilascio ne azzera il contenuto. Cancellarli
  sostituirebbe l'inode e renderebbe inefficace il lock di chi li tiene aperti.
- Il registry è protetto da un lock su `registry.md.lock`, file dedicato e mai rinominato,
  distinto dal file che viene scritto.
- L'identità di un lock è il path **reale** risolto: path relativi, assoluti e symlink
  allo stesso file contendono lo stesso lock; file omonimi in progetti diversi no.
- Un lock non rinnovato per oltre 120s è stale e può essere acquisito da un altro agente:
  è ciò che impedisce a un agente crashato di bloccare un file per sempre.
- Timestamp in timezone Roma (`Europe/Rome`).
