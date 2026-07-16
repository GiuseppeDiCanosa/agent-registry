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

### Lock via `os.open(O_CREAT|O_EXCL)` invece di `fcntl.flock`

`O_CREAT|O_EXCL` fallisce con `EEXIST` se il file esiste già, e la verifica-e-creazione è atomica a livello di syscall — nessuna finestra tra il test e la creazione. Soprattutto: **l'esito è un file su disco, che sopravvive alla morte del processo**. È l'unica primitiva POSIX che dà mutua esclusione con la vita utile richiesta dal nostro modello (il lock deve durare *più* del processo che lo prende).

Alternative considerate:
- *Mantenere flock con un processo daemon long-lived*: costringerebbe ogni agente a gestire il ciclo di vita di un demone, e la morte del demone rilascerebbe tutto in blocco. Complessità sproporzionata.
- *`fcntl.lockf`*: stesso identico problema di vita legata al processo.
- *Directory come lock (`mkdir`)*: anch'essa atomica, ma non permette di scrivere owner e timestamp nello stesso oggetto; servirebbe un file dentro la directory, con una seconda finestra di race.
- *SQLite*: darebbe transazionalità vera, ma introduce una dipendenza concettuale pesante e rende il lock non ispezionabile con `cat`, perdendo il valore diagnostico del formato attuale.

Il rovescio di O_EXCL: siccome nessuno rilascia il lock quando il processo muore, un agente che crasha lascia il lock appeso. È esattamente ciò che il meccanismo di staleness già previsto (timestamp + timeout) deve coprire — quindi lo scambio è: perdiamo il rilascio automatico del kernel (che comunque non ci serviva, era il bug) e ci teniamo la scadenza esplicita.

### La staleness si risolve con takeover atomico, non con "unlink e riprova"

La 0.1.0 in `is_locked` faceva `unlink()` del lock stale e tornava "libero": due agenti che osservano lo stesso lock stale nello stesso istante lo cancellano entrambi e lo acquisiscono entrambi. Il takeover deve essere atomico.

Approccio: scrivere il lock candidato in un file temporaneo nella stessa directory e tentare `os.link(tmp, lock_file)` — `link()` è atomica e fallisce con `EEXIST` se la destinazione esiste. Per il takeover di uno stale: rimuovere il vecchio lock **solo se non è cambiato nel frattempo**, confrontando l'identità del file (`st_ino`, `st_mtime_ns`) letta prima e dopo. Chi arriva secondo trova un inode diverso e rinuncia. Nessuna finestra in cui due agenti si credono entrambi owner.

Alternativa considerata: `os.rename(tmp, lock_file)` è atomica ma **sovrascrive silenziosamente** la destinazione — è precisamente la primitiva sbagliata qui, ed è moralmente lo stesso errore della 0.1.0 (`_write_info` sovrascriveva l'owner senza guardare).

### Il registry si serializza con un lock file dedicato e mai rinominato

`registry.lock` è un file separato che esiste solo per essere lockato con `flock`, e che **nessuno rinomina o cancella**. `registry.md` continua a essere scritto atomicamente via `tempfile` + `os.replace` — che è corretto per la scrittura, e ora non interferisce più col lock perché il lock vive su un altro inode.

Qui `flock` è la primitiva **giusta**, al contrario che nel lock manager: la sezione critica dura quanto il ciclo read-modify-write, cioè millisecondi *dentro un solo processo vivo*. Il rilascio automatico alla morte del processo, che nel lock manager era il bug, qui è la proprietà desiderata: un agente che crasha a metà scrittura non lascia il registry bloccato per sempre. Stessa syscall, verdetto opposto, perché cambia la durata richiesta.

Il ciclo diventa: prendi `flock(registry.lock)` → leggi `registry.md` → modifica → scrivi via replace → rilascia. Lettura e scrittura nella stessa sezione critica: niente TOCTOU.

### `finish` rilascia i lock, eliminando la seconda fonte di verità

Il registry (`do_not_touch`) e la directory `locks/` sono due rappresentazioni dello stesso fatto, e la 0.1.0 le lasciava divergere chiedendo all'agente di allinearle a mano. `unregister_session` ora rilascia i lock della sessione, saltando quelli di cui non è owner. Il registry resta la vista leggibile; i file di lock restano il meccanismo autorevole.

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
