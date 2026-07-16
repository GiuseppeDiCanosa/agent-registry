# Work Review — fix-cross-process-coordination

Review requisito per requisito con evidenza `file:riga`. 17 requisiti (8 `file-locking`,
9 `agent-registry`), 42 scenari. Suite: **59/59 verdi**; `verify.sh`: link `[@test]`,
target ownership e manifest tutti verdi.

## file-locking → `scripts/lock_manager.py`

| Requisito | Implementazione | Verificato da |
|---|---|---|
| Mutua esclusione fra processi one-shot | `acquire_lock` `lock_manager.py:168`; stato nel contenuto `_serialize:71` / `_read_fd:115`; `_critical:95` serializza senza rappresentare la proprietà | `test_lock_cross_process.py` — `test_second_agent_cannot_acquire_valid_lock`, `test_owner_keeps_lock_after_failed_takeover`, `test_concurrent_acquire_elects_single_winner`, `test_distinct_paths_do_not_interfere` |
| Riacquisizione idempotente dell'owner | `lock_manager.py:178-180` | `test_owner_reacquire_is_idempotent` |
| Scadenza dei lock abbandonati | `_is_stale:141`; takeover dentro la sezione critica `lock_manager.py:182-187` | `test_stale_lock_is_detected`, `test_stale_lock_can_be_acquired`, `test_stale_takeover_race_has_single_winner` |
| Rinnovo riservato all'owner | `heartbeat:221`, verifica owner `:230-236` | `test_owner_can_heartbeat`, `test_non_owner_cannot_heartbeat`, `test_heartbeat_on_missing_lock_fails`, `test_heartbeat_prevents_expiry` |
| Rilascio riservato all'owner | `release_lock:200`, verifica owner `:208-214`; `_clear_fd:136` azzera senza unlink | `test_owner_can_release`, `test_non_owner_cannot_release`, `test_release_missing_lock_is_noop` |
| Identità indipendente dalla cwd | `_lock_file:55` via `os.path.realpath` | `test_relative_and_absolute_path_are_same_lock`, `test_same_name_different_projects_do_not_collide` |
| Directory dei lock configurabile | `get_lock_dir:43` (risolta a ogni chiamata); `_ensure_lock_dir:67` | `test_lock_dir_env_override_is_respected`, `test_lock_dir_is_created_when_missing` |
| CLI con exit code significativi | `main:294`, `USAGE:285`, `IndexError` → uso senza traceback `:318-321` | `test_lock_cli.py` — 8 test |

## agent-registry → `scripts/registry_manager.py`

| Requisito | Implementazione | Verificato da |
|---|---|---|
| Aggiornamenti concorrenti che non si perdono | `_registry_critical:98` su `_lock_path:88` (file dedicato, mai rinominato); RMW dentro la sezione critica `register_session:261-265`, `update_session:276-286` | `test_registry_concurrency.py` — `test_concurrent_registrations_are_all_preserved`, `test_concurrent_updates_are_all_preserved`, `test_registry_stays_parsable_under_concurrent_writes` |
| Registrazione di una sessione | `register_session:236`; sostituzione per id uguale `:262` | `test_register_new_session`, `test_reregister_same_id_replaces` |
| Aggiornamento dei campi | `update_session:270`; `None` se assente `:288` | `test_partial_update_preserves_other_fields`, `test_update_unknown_session_does_not_create_it`, `test_update_todo_merges_subfields` |
| Chiusura con rilascio dei lock | `unregister_session:290`; rilascio `:317-326`, owner-only delegato a `release_lock` | `test_finish_releases_session_locks`, `test_finish_does_not_release_other_sessions_locks`, `test_unregister_marks_finished` |
| Percorso configurabile | `get_registry_path:78` (risolto a ogni chiamata); `ensure_registry:203` | `test_registry_path_env_override`, `test_registry_parent_dirs_are_created`, `test_ensure_registry_creates_valid_skeleton` |
| Leggibile da umani e macchine | `_dump_registry:163`; `_fmt:130`, `_fmt_list:137` (escape anche nelle liste) | `test_table_matches_frontmatter`, `test_pipe_and_newline_are_escaped_in_scalars`, `test_pipe_is_escaped_inside_lists` |
| Protocollo auto-descrittivo | `PROTOCOL_BLOCK:38`, `get_protocol_block:68`; rigenerato in `_dump_registry:169` | `test_registry_protocol.py` — 6 test |
| Riferimento all'handoff | `add_handoff_ref:335` | `test_add_handoff_ref` |
| CLI con exit code significativi | `main:350`; operazione non avvenuta → exit 1 `:367-370`, `:374-377`, `:381-384` | `test_registry_cli.py` — 7 test |

## Prove dirette dei difetti originali

Rieseguite con la CLI documentata, non con i test:

```
Agente A acquisisce auth.py   → {'locked': True, 'owner': 'claude-111'}   exit=0
Agente B (2s dopo, timeout 120s) → {'locked': False, 'session_id': 'claude-111'} exit=1
A ricontrolla                 → {'ok': True, 'note': 'already locked by you'} exit=0

8 agenti in parallelo → TROVATI: 8 / 8
```

Prima: B riceveva `locked: True` rubando il lock, A lo perdeva senza saperlo; 1 sola
registrazione su 8 sopravviveva.

## Scostamenti dal piano, dichiarati

1. **L'approccio ai lock è cambiato durante l'implementazione.** `design.md` prevedeva
   `O_EXCL` + `os.link()` con takeover verificato su `st_ino`/`st_mtime_ns`. È sbagliato:
   `unlink` agisce sul *nome*, non sull'inode, quindi non esiste "cancella solo se è
   ancora quello che ho letto" e due taker possono cancellare l'uno il lock dell'altro.
   Sostituito da: stato nel contenuto del file + `flock` sulla sola sezione critica.
   `design.md` e `tasks.md` (4.3, 4.4) sono stati aggiornati, non lasciati a mentire.

2. **Requisito aggiunto in corsa**: "Protocollo di coordinamento auto-descrittivo", su
   richiesta esplicita durante il lavoro. Inserito prima nella spec, poi implementato.

3. **`test_pipe_is_escaped_inside_lists` è stato corretto**, non il codice: contava anche
   le pipe già neutralizzate (`\|`). Verificato con mutation test che il test riformulato
   fallisce reintroducendo il bug 0.1.0 in `_fmt_list`.

4. **`test_concurrent_acquire_elects_single_winner` passava a intermittenza sulla 0.1.0**:
   con la barriera i processi si sovrappongono davvero e in quell'istante `flock` funziona.
   Non era un buon discriminante del difetto, ma resta un test valido del requisito.

5. **`test_finish_releases_session_locks` passava sulla 0.1.0 per il motivo sbagliato**:
   `finish` non rilasciava nulla, ma `acquire_lock` rubava comunque il lock, quindi
   l'asserzione finale era soddisfatta. Ora passa per il motivo giusto.

## Limiti noti, non risolti qui

- **I lock restano advisory**: nulla impedisce a un agente che ignora il protocollo di
  scrivere su un file bloccato. Dichiarato in `SKILL.md` e nel blocco di protocollo.
- **I lock file non vengono mai cancellati** (un file vuoto per path mai lockato): è il
  prezzo diretto della correttezza, documentato in `design.md`.
- **Il default `~/Desktop` resta invariato**: mono-macchina, mono-progetto ed esposto a
  iCloud. Fuori scope — questo change risolve la concorrenza, non la scelta del percorso.
  `SKILL.md` raccomanda ora `AGENT_REGISTRY_PATH` fuori dalle cartelle sincronizzate.
  Da affrontare in un change dedicato (vedi *Open Questions* in `design.md`).
- **Il nome pubblicato conserva il refuso** `spec-driven-devlopment`: cambiarlo è una
  decisione di pubblicazione, non tecnica.

## Esito

Tutti i 17 requisiti hanno implementazione e verifica tracciabili. Nessun requisito
implementato fuori dalla spec. Nessun `[@test]` senza file su disco. Target allineati
alle spec che li dichiarano.

**Pronto per `openspec-sync-specs` + `openspec-archive-change`.**
