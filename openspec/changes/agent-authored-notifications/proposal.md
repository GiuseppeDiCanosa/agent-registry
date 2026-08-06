## Why

Le notifiche WhatsApp escono da un pool di stringhe statiche in cui la firma è
incollata dentro al testo: le 24 voci di `notifier/messages.local.json` finiscono
tutte con `— Codex`, quindi anche gli eventi delle sessioni Claude arrivano
firmati Codex. La firma mente, e con un terzo provider mentirebbe di più. Gli
agenti che quel testo potrebbero scriverlo davvero — con la propria voce e il
proprio nome — sono già in esecuzione e già scrivono nel registry: manca solo il
posto dove depositare il messaggio.

## What Changes

- I file `sessions/<id>.yaml` acquisiscono un campo `notify`, una chiave per
  evento, in cui l'agente deposita il testo che ha composto.
- `registry_manager.py` accetta il testo al momento di registrare e di chiudere
  una sessione, tramite parametri **facoltativi**: nessuna chiamata esistente si
  rompe.
- `notifier/watchdog.py` spedisce il testo composto quando c'è, poi lo rimuove
  dal file di sessione (consumo distruttivo) sotto il lock di `lock_manager`,
  ripristinando l'mtime originale — che è la sorgente di `last_activity` e quindi
  del rilevamento di inattività.
- La firma non è più contenuta nelle stringhe del pool: viene applicata a runtime
  dal provider della sessione, sia sul testo composto sia sul fallback. Un terzo
  provider firma col proprio nome senza modifiche a `watchdog.py`.
- La `SKILL.md` guadagna una sezione che istruisce ogni agente a comporre il
  proprio messaggio. È un'istruzione, non un vincolo: chi non compone si degrada
  in silenzio sul pool statico.

Nessun **BREAKING**: i parametri sono facoltativi e il comportamento senza campo
`notify` è quello odierno.

## Capabilities

### New Capabilities

Nessuna.

### Modified Capabilities

- `whatsapp-notifications`: la selezione del messaggio non è più solo
  `random.choice` sul pool — precede il testo composto dall'agente, se presente;
  la firma diventa un elemento applicato a runtime anziché parte del template; il
  watchdog acquisisce il diritto di scrivere nei file di sessione per consumare
  il messaggio.
- `agent-registry`: i file di sessione acquisiscono il campo `notify` e la CLI i
  parametri facoltativi per popolarlo.

## Impact

- `notifier/watchdog.py`, `notifier/messages.default.json` — target della spec
  `whatsapp-notifications`.
- `scripts/registry_manager.py`, `scripts/lock_manager.py` (usato, non
  modificato) — area della spec `agent-registry`.
- `SKILL.md` — sezione di istruzione agli agenti.
- `notifier/messages.local.json` è **gitignored**: la ripulitura delle sue firme
  è un'operazione locale che nessun commit può portarsi dietro.
- Il watchdog scrive per la prima volta nei file di sessione, finora di sola
  proprietà degli agenti: è il punto di rischio del change, mitigato dal lock.
