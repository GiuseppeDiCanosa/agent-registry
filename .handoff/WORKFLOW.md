# WORKFLOW.md — Metodologia spec-as-source applicata ad agent-registry
> Aggiornato: 2026-07-17
> Documento vivo. Template adattato: progetto software, non creativo.
> Regole canoniche: `~/spec-as-source/rules/`

## Pipeline standard

### 1. Investigare prima di proporre
Riprodurre il problema con **comandi reali e output osservato**, mai su ipotesi.
Un difetto "teoricamente possibile" non entra in un proposal: si mostra vincente.

### 2. `openspec-propose` — il change e i suoi artifact
```bash
openspec new change "<nome-kebab>"
openspec status --change "<nome>" --json          # ordine degli artifact
openspec instructions <artifact> --change "<nome>" --json
```
Ordine imposto dallo schema `spec-as-source`: **proposal → specs → design → tasks**.
`context` e `rules` nelle istruzioni sono vincoli per te, **non** contenuto da copiare.

### 3. `spec-writer` — la spec è il contratto
```markdown
---
targets:
  - scripts/file.py
---
### Requirement: <nome>
Frase normativa SHALL/MUST.

**Verified by**: [@test] tests/test_file.py

#### Scenario: <nome>          ← esattamente 4 hash, o fallisce in silenzio
- **WHEN** ...
- **THEN** ...
```
`**Verified by**` va **dopo** la frase normativa, mai come prima riga del blocco.
Validare sempre: `openspec validate "<nome>" --strict`.

### 4. Test rossi **prima** del codice
I test si scrivono contro la spec e devono **fallire** sull'implementazione vecchia.
Registrare il rosso di partenza per confrontarlo col verde finale.
Se un test passa già su codice rotto, capire perché: o non discrimina, o passa per
il motivo sbagliato — in entrambi i casi va annotato.

### 5. `openspec-apply-change` — implementare
Header obbligatorio su ogni file in `targets:`:
```python
# GENERATED FROM SPEC — DO NOT EDIT DIRECTLY
# Source: openspec/specs/<capability>/spec.md
```
**Se durante l'implementazione il design si rivela sbagliato, si aggiorna il design,
non solo il codice.** Successo in questa sessione: `design.md` prevedeva `O_EXCL` +
`os.link()`; implementando è emerso che non regge il takeover di un lock stale.
Corretti design.md e tasks.md, non solo il codice.

### 6. `spec-verify`
```bash
bash scripts/verify.sh
```
Verde = link `[@test]` risolti + target allineati alle spec + suite passata.

### 7. `work-review` — prima di dire "fatto"
Requisito per requisito, con evidenza `file:riga`, e **verificare che le righe citate
siano davvero quelle** (una review con evidenze sbagliate è peggio di nessuna review).
Dichiarare gli scostamenti dal piano, non nasconderli.

### 8. Chiudere
```bash
openspec archive <nome-change> --yes
```
⚠️ **L'archive rimuove il frontmatter `targets:` e lascia `Purpose: TBD`** sulle spec
di capability nuove. Vanno ripristinati subito: senza `targets`, `check-target-ownership`
passa **a vuoto** — un check che non verifica è peggio di un check assente.

## Testing della concorrenza — regole non negoziabili

Il modello d'esecuzione reale è: **comandi one-shot che muoiono subito**.

- Ogni test di concorrenza gira in **processi separati che terminano davvero**.
- Per far collidere N processi serve una **barriera di wall-clock condivisa**: senza,
  lo startup di Python (~50ms) li scaglions e si serializzano da soli, nascondendo
  proprio la race che vuoi osservare.
- Isolare **via ambiente**, mai via monkeypatch: il monkeypatch funziona solo
  in-process, ed è ciò che ha costretto la 0.1.0 a test ciechi. Il design non
  testabile produce i test ciechi.
- Reindirizzare `HOME` nella fixture: un codice che ignora l'override ricade sul
  default e scrive nella home reale.

## Convenzioni

- Versioni: semver in `.tessl-plugin/plugin.json` (unica fonte; `tile.json` è sintetizzato).
- Commit: messaggio che spiega **perché**, non cosa. I difetti provati vanno citati.
- Branch: `main`, push diretto (progetto a operatore singolo).

## Archivio sessioni
`.handoff/HANDOFF-NNN.md` — append-only, mai modificare i precedenti.
