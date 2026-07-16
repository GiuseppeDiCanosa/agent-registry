## Context

`agent-registry` coordina agenti AI CLI eterogenei (Claude, Kimi, Gemini, Codex…) che lavorano sullo stesso repo. Il vincolo che determina tutto il design è il **modello di esecuzione degli agenti**: un agente non è un servizio long-lived, è una sequenza di comandi bash one-shot. `python lock_manager.py acquire src/auth.py sid` parte, fa il suo lavoro e muore in poche decine di millisecondi.

La 0.1.0 ha ignorato questo vincolo e ha costruito la mutua esclusione su `fcntl.flock`, che il kernel rilascia alla chiusura dell'ultimo fd — cioè all'uscita del processo. Il risultato è che il lock esiste solo durante la vita del comando che lo prende, e il comando successivo trova campo libero. Lo stesso vincolo spiega perché `_OPEN_LOCK_FDS`, il dict in memoria che nei test garantiva la mutua esclusione, in produzione è sempre vuoto: ogni comando è un processo nuovo.

Il secondo difetto è indipendente dal primo: `save_agents` prende il flock sull'fd di `registry.md` e poi lo sostituisce con `shutil.move`. Dopo il rename il path punta a un inode nuovo, e il writer successivo mette il lock su un oggetto diverso da quello del writer precedente. Due writer che credono di escludersi a vicenda stanno lockando file diversi.

## Goals / Non-Goals

**Goals:**
- Mutua esclusione reale fra processi one-shot che terminano subito dopo l'acquisizione.
- Nessuna perdita di aggiornamenti concorrenti sul registry.
- Test che esercitino il modello d'esecuzione reale (processi separati che muoiono), non una sua simulazione comoda.
- Nessuna dipendenza esterna nuova: solo stdlib.

**Non-Goals:**
- Coordinamento fra macchine diverse. Il registry resta locale a un filesystem; NFS e simili non sono supportati (vedi Risks).
- Lock a livello di riga o di simbolo: la granularità resta il path.
- Impedire fisicamente la scrittura su un file locked. Il lock è **advisory**: protegge gli agenti che lo consultano, non i `write()` di chi lo ignora. La SKILL.md deve dirlo esplicitamente.
- Sostituire la webapp o cambiare il formato del registry.

## Decisions

### Separare il *meccanismo di esclusione* dallo *stato che sopravvive*

L'errore concettuale della 0.1.0 non è "ha usato flock": è aver chiesto a flock di essere **lo stato**. flock vive quanto il processo; la proprietà di un lock deve vivere quanto il lavoro. Da qui la separazione che regge tutto il design:

- **Lo stato** — chi possiede il path e da quando — sta nel **contenuto di un file**, che sopravvive alla morte del processo. È l'unica cosa che decide se un path è occupato.
- **La mutua esclusione durante l'aggiornamento** di quello stato è garantita da `flock`, tenuto per la sola durata della sezione critica read-modify-write, interamente dentro un processo vivo.

Con questa separazione flock è la primitiva giusta e il suo rilascio automatico alla morte del processo diventa una proprietà desiderabile: un agente che crasha a metà aggiornamento non lascia il lock file bloccato per sempre. Lo stesso ragionamento vale identico nel registry — stessa syscall, stesso verdetto, perché in entrambi i casi la durata richiesta è "un istante dentro un processo vivo".

**Vincolo che rende il tutto corretto: il file di lock non viene mai cancellato né sostituito.** Un rilascio azzera il contenuto, non rimuove il file. Se il file venisse unlinkato, due processi potrebbero tenere fd su inode diversi e flock non escluderebbe più nulla — è esattamente il modo in cui `shutil.move` aveva neutralizzato il lock del registry nella 0.1.0. Per la stessa ragione l'aggiornamento avviene in-place (`ftruncate` + `write`) e mai con `os.replace`. I lettori prendono un flock condiviso, quindi non osservano mai una scrittura a metà.

Alternative considerate:
- *`os.open(O_CREAT|O_EXCL)` + `os.link()`*: era l'approccio previsto in prima stesura. Dà un vincitore unico sull'acquisizione, ma **non risolve il takeover di un lock stale**: rimuovere lo stale richiede `unlink()`, che agisce sul *nome* e non sull'inode, quindi non esiste modo di dire "cancella solo se è ancora quello che ho letto". Due agenti che osservano lo stesso stale possono unlinkare l'uno il lock appena creato dall'altro e credersi entrambi owner. Verificare `st_ino`/`st_mtime_ns` prima e dopo non chiude la finestra, perché fra il controllo e l'unlink non c'è atomicità. Scartato per questo.
- *Daemon long-lived che tiene i flock*: costringerebbe ogni agente a gestire il ciclo di vita di un demone, e la sua morte rilascerebbe tutto in blocco.
- *SQLite*: transazionalità vera, ma dipendenza pesante e lock non più ispezionabile con `cat`, perdendo il valore diagnostico del formato attuale.

Il rovescio: siccome il kernel non rilascia più nulla alla morte del processo, un agente che crasha lascia il lock appeso fino alla scadenza. È precisamente ciò che il meccanismo di staleness (timestamp + timeout) copre — lo scambio è consapevole: perdiamo un rilascio automatico che comunque non volevamo (era il bug) e ci teniamo una scadenza esplicita e osservabile.

### La staleness si risolve dentro la sezione critica, non con "unlink e riprova"

La 0.1.0 in `is_locked` faceva `unlink()` del lock stale e restituiva "libero": due agenti che osservano lo stesso stale nello stesso istante lo cancellano entrambi e lo acquisiscono entrambi.

Con lo stato nel contenuto e flock a proteggere l'aggiornamento, il takeover non richiede alcuna primitiva speciale: dentro la sezione critica si rilegge il contenuto, si valuta la scadenza e si scrive il nuovo owner. Due taker sono serializzati dal flock, il secondo rilegge e vede il primo come owner fresco. `is_locked` diventa un lettore puro e non cancella più nulla: osservare non modifica.

### Costo accettato: i file di lock non spariscono

Non cancellare mai i lock file significa che ogni path mai lockato lascia un file (vuoto quando rilasciato) in `locks/`. Sono pochi byte per path e restano ispezionabili con `cat`. È il prezzo diretto della correttezza — ed è preferibile all'alternativa, dove la pulizia introduce di nuovo la sostituzione di inode che ha rotto la 0.1.0.

### Il registry si serializza con un lock file dedicato e mai rinominato

`registry.lock` è un file separato che esiste solo per essere lockato con `flock`, e che **nessuno rinomina o cancella**. `registry.md` continua a essere scritto atomicamente via `tempfile` + `os.replace` — che è corretto per la scrittura, e ora non interferisce più col lock perché il lock vive su un altro inode.

Qui `flock` è la primitiva **giusta**, al contrario che nel lock manager: la sezione critica dura quanto il ciclo read-modify-write, cioè millisecondi *dentro un solo processo vivo*. Il rilascio automatico alla morte del processo, che nel lock manager era il bug, qui è la proprietà desiderata: un agente che crasha a metà scrittura non lascia il registry bloccato per sempre. Stessa syscall, verdetto opposto, perché cambia la durata richiesta.

Il ciclo diventa: prendi `flock(registry.lock)` → leggi `registry.md` → modifica → scrivi via replace → rilascia. Lettura e scrittura nella stessa sezione critica: niente TOCTOU.

### `finish` rilascia i lock, eliminando la seconda fonte di verità

Il registry (`do_not_touch`) e la directory `locks/` sono due rappresentazioni dello stesso fatto, e la 0.1.0 le lasciava divergere chiedendo all'agente di allinearle a mano. `unregister_session` ora rilascia i lock della sessione, saltando quelli di cui non è owner. Il registry resta la vista leggibile; i file di lock restano il meccanismo autorevole.

### Il protocollo viaggia col registry, non con la skill

Gli agenti che questa skill deve coordinare appartengono a provider diversi — Claude, Kimi, Gemini, Codex — che **non condividono alcun sistema di skill**: `SKILL.md` istruisce solo chi la carica, cioè in pratica solo Claude. Un agente che non l'ha caricata non sa che il registry esiste, né che deve rispettarlo.

L'unico artefatto che tutti toccano è il registry stesso. Mettere le regole di coordinamento nel file significa che chiunque lo apra — qualsiasi CLI, o un umano — le legge nel momento esatto in cui gli servono. Le istruzioni viaggiano con lo stato che descrivono.

Il blocco è **rigenerato a ogni scrittura** e non semplicemente scritto alla creazione: un blocco che si può perdere con un update è un blocco su cui non si può contare, e il caso peggiore (un agente che legge un registry senza regole e conclude che non ce ne sono) è proprio quello da escludere. La rigenerazione lo rende anche auto-riparante rispetto alle manomissioni.

Il blocco vive fra frontmatter e tabella e non è mai una fonte di dati: il frontmatter resta l'unico dato autorevole, il blocco è testo per il lettore. Deve inoltre dichiarare esplicitamente che i lock sono **advisory** — un agente che crede in una garanzia che non esiste è il problema da cui è nato questo change, e ripeterlo a livello di documentazione sarebbe la stessa classe di errore.

### `os.path.realpath` per l'identità del lock

La 0.1.0 usava `abspath`, che non risolve i symlink: due path che puntano allo stesso file reale otterrebbero due lock distinti. `realpath` chiude il buco. Resta l'hash SHA-256 troncato a 16 hex come nome del file di lock (collisione trascurabile), ma il path reale va scritto **dentro** il lock, così un umano che ispeziona `locks/` capisce cosa è bloccato senza dover invertire un hash.

## Risks / Trade-offs

- **Filesystem di rete (NFS)** → `O_EXCL` e `flock` hanno garanzie deboli o rotte su NFS. Mitigazione: documentare in SKILL.md che registry e lock devono stare su un filesystem locale. Il default `~/Desktop` è particolarmente esposto perché spesso sincronizzato da iCloud Drive, che può generare copie in conflitto proprio sul file autorevole: la SKILL.md deve raccomandare `AGENT_REGISTRY_PATH` fuori dalle cartelle sincronizzate.
- **Lock advisory** → un agente che non consulta il registry scrive comunque su un file locked. Mitigazione: nessuna tecnica possibile a questo livello; va dichiarato nella skill come limite di progetto, non nascosto.
- **Crash fra `link()` e la scrittura del contenuto** → un lock esiste ma è illeggibile. Mitigazione: il contenuto viene scritto nel file temporaneo *prima* del `link()`, quindi il lock è completo nell'istante in cui diventa visibile. La sequenza scrivi-poi-pubblica è ciò che rende l'operazione atomica anche rispetto ai crash.
- **Orologio di sistema che salta all'indietro** → un lock potrebbe sembrare più giovane del vero e scadere tardi. Mitigazione: `time.time()` è accettabile perché il confronto è fra timestamp scritti da processi diversi, dove un orologio monotonico per-processo non sarebbe comparabile. Rischio accettato, di impatto limitato al ritardo di una scadenza.
- **Test di concorrenza flaky** → i test sparano N processi reali e verificano che esattamente uno vinca; su CI lenta i tempi variano. Mitigazione: le asserzioni contano i vincitori, non misurano durate. Solo i test di staleness dipendono dal tempo, usano timeout brevi (0.2–1s) e sono marcati `slow`.

## Migration Plan

L'API pubblica mantiene i nomi esistenti (`acquire_lock`, `release_lock`, `heartbeat`, `is_locked`, `check_and_warn`, `guarded_acquire`, `register_session`, `update_session`, `unregister_session`, `add_handoff_ref`), quindi la webapp e ogni chiamante esistente continuano a funzionare senza modifiche.

Cambia la **semantica nei conflitti**, ed è un cambiamento voluto: chi prima rubava un lock e riceveva `{'locked': True}` ora riceve `{'locked': False}` con l'owner corrente. Qualsiasi codice che assumesse il successo incondizionato di `acquire_lock` era già rotto — assumeva una garanzia che non esisteva.

I lock file della 0.1.0 hanno lo stesso formato (`session_id|timestamp`) e restano leggibili; nessuna migrazione dei dati è necessaria. Il rollback è il ripristino del commit precedente, senza conversioni.

## Open Questions

- Il default `~/Desktop/agent-registry/` va cambiato? È mono-macchina e mono-progetto, e su Desktop rischia la sincronizzazione iCloud. Un default per-progetto (`.agent-registry/` nella root del repo) sarebbe più corretto ma cambierebbe il comportamento per gli utenti della 0.1.0. **Fuori scope di questo change**: qui si risolve la correttezza della concorrenza, non la scelta del percorso. Da valutare in un change dedicato.
- La granularità del lock resta il file. Con agenti che lavorano su moduli grandi il lock su directory potrebbe servire, ma non c'è ancora evidenza d'uso che lo giustifichi.
