# PROMPTS.md — Comandi e Ricette Validate
> Progetto: agent-registry | Aggiornato: 2026-07-17
> Template adattato: questo è un progetto software, non creativo. Al posto dei prompt
> di generazione immagini trovi i comandi validati in sessione, con il perché.
> Aggiungere solo dopo averli eseguiti davvero.

---

## Verifica

### [VER-001] — Verifica completa (spec + test)
```bash
cd ~/Claude-Projects/agent-registry
bash scripts/verify.sh
```
- Esegue: `check-spec-links` ([@test] risolvono a file esistenti), `check-target-ownership`
  (nessun target modificato senza la sua spec), `build-spec-manifest`, poi pytest.
- Atteso: 3 PASSED + `59 passed`.
- Data: 2026-07-16

### [VER-002] — Solo i test
```bash
python3 -m pytest tests/ -q          # tutti
python3 -m pytest tests/ -q -m "not slow"   # esclude quelli con attese reali
```
- I test `slow` sono quelli su staleness/heartbeat: dipendono dal tempo, timeout 0.2–1s.
- Data: 2026-07-16

### [VER-003] — Mutation test di un test (verificare che sappia fallire)
```bash
cp scripts/registry_manager.py /tmp/backup.py
# reintrodurre il bug, es. in _fmt_list togliere l'escape:
#   return ", ".join(str(x) for x in items)
python3 -m pytest tests/test_registry_manager.py::test_pipe_is_escaped_inside_lists -q
cp /tmp/backup.py scripts/registry_manager.py
```
- **Perché conta**: un test che non sa fallire non verifica nulla. Usato per validare
  `test_pipe_is_escaped_inside_lists` dopo averlo corretto.
- Data: 2026-07-16

---

## Riproduzione dei difetti storici (0.1.0)

### [BUG-001] — Furto del lock
```bash
export AGENT_REGISTRY_LOCK_DIR=/tmp/probe/locks
python3 scripts/lock_manager.py acquire /tmp/probe/auth.py "claude-111"; echo "exit=$?"
sleep 2
python3 scripts/lock_manager.py acquire /tmp/probe/auth.py "kimi-222";  echo "exit=$?"
```
- 0.1.0: B otteneva `{'locked': True}` → lock rubato.
- 0.2.x: B ottiene `{'locked': False, 'session_id': 'claude-111'}`, **exit=1**.
- Data: 2026-07-16

### [BUG-002] — Registrazioni concorrenti perse
```bash
export AGENT_REGISTRY_PATH=/tmp/probe/registry.md
for i in 1 2 3 4 5 6 7 8; do
  python3 scripts/registry_manager.py register "agent-$i" "P$i" v1 "task $i" &
done; wait
python3 -c "import sys;sys.path.insert(0,'scripts');from registry_manager import load_agents;print(len(load_agents()))"
```
- 0.1.0: 8 successi riportati, **1** sopravvissuto.
- 0.2.x: **8**.
- Data: 2026-07-16

---

## Pubblicazione

### [PUB-001] — Pubblicare su Tessl da directory di staging
```bash
STAGE=/tmp/publish-stage && rm -rf $STAGE && mkdir -p $STAGE
cd ~/Claude-Projects/agent-registry
cp SKILL.md README.md LICENSE pytest.ini requirements-dev.txt $STAGE/
mkdir -p $STAGE/.tessl-plugin && cp .tessl-plugin/plugin.json $STAGE/.tessl-plugin/
cp -R templates tests $STAGE/
mkdir -p $STAGE/scripts
cp scripts/lock_manager.py scripts/registry_manager.py scripts/requirements.txt $STAGE/scripts/
cp -R scripts/webapp $STAGE/scripts/
find $STAGE -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null

python3 -m pytest $STAGE/tests -q        # il pacchetto deve reggersi da solo
npx tessl plugin publish --dry-run $STAGE
npx tessl plugin publish $STAGE
```
- **Perché lo staging**: pubblicare dal repo includerebbe `.claude/skills/openspec-*`
  e `.claude/commands/opsx/*` — skill di OpenSpec, non nostre: le ridistribuirebbe e
  le inietterebbe nel progetto di chi installa. Non esiste `.tesslignore`; l'unica
  leva è il **path** che si passa a `publish`.
- Escludere anche: `openspec/` (le spec stanno su GitHub), CI, pre-commit,
  `scripts/check-*.sh`, `verify.sh` (tooling di sviluppo).
- Ricordarsi di bumpare `version` in `.tessl-plugin/plugin.json` prima.
- **Non creare `tile.json`**: è sintetizzato da `plugin.json` in fase di pack.
- Data: 2026-07-16

### [PUB-002] — Verificare cosa è davvero online
```bash
npx tessl plugin info spec-driven-development/agent-registry | grep -iE "Latest|Visibility|Moderation"
# e la prova che conta — installazione pulita non pinnata:
mkdir /tmp/fresh && cd /tmp/fresh && npx tessl i spec-driven-development/agent-registry
cat .tessl/plugins/spec-driven-development/agent-registry/tessl-package.json
```
- **Perché**: dopo il publish la moderazione impiega qualche minuto a spostare `latest`.
  Subito dopo aver pubblicato 0.2.1, un install non pinnato dava ancora 0.2.0.
- Data: 2026-07-16

---

## Prova end-to-end della skill

### [E2E-001] — Due agenti che si contendono un file
```bash
cd ~/Claude-Projects/prova-agent-registry
SKILL_DIR="./.tessl/plugins/spec-driven-development/agent-registry/scripts"
export AGENT_REGISTRY_PATH="$PWD/.agent-registry/registry.md"
export AGENT_REGISTRY_LOCK_DIR="$PWD/.agent-registry/locks"

python3 "$SKILL_DIR/registry_manager.py" register "claude-1" Claude "Opus 4.8" "refactor" "src/auth.py" "analisi"
python3 "$SKILL_DIR/lock_manager.py" acquire "src/auth.py" "claude-1"   # exit 0
python3 "$SKILL_DIR/lock_manager.py" acquire "src/auth.py" "kimi-2"     # exit 1 = bloccato
python3 "$SKILL_DIR/registry_manager.py" finish "claude-1"              # rilascia anche il lock
```
- Data: 2026-07-17

### [E2E-002] — Dashboard
```bash
pip install uvicorn fastapi   # NON erano installati su questa macchina
cd "$SKILL_DIR/webapp" && python3 -m uvicorn main:app --host 127.0.0.1 --port 8765
# stop: lsof -ti:8765 | xargs kill
```
- Data: 2026-07-16
