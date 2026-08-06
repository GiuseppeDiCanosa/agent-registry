# GENERATED FROM SPEC — DO NOT EDIT DIRECTLY
# Source: openspec/specs/whatsapp-notifications/spec.md
"""Watchdog: osserva lo stato del registry e notifica via WhatsApp quattro eventi.

- started:  una sessione passa a OnWorking (inclusa la prima comparsa nel registry)
- executed: una sessione passa a Finished
- stopped:  una sessione passa a Stop o Killed
- idle:     una sessione OnWorking senza attività da oltre la soglia (default 3600s)

`classify_events` e `render_message` sono funzioni pure e testabili; `main` legge lo
stato da AGENT_REGISTRY_HOME, invia i messaggi e persiste lo stato per non duplicare le
notifiche.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wa_client  # noqa: E402  (import top-level, coerente col runtime nel container)

STOPPED_STATES = ("Stop", "Killed")
PLACEHOLDER_KEYS = ("name", "session_id", "provider", "working_on", "minutes")


def classify_events(
    sessions: list[dict[str, Any]],
    prev_state: dict[str, Any],
    now: float,
    idle_threshold: float,
    cold_start: bool = False,
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    """Classifica le sessioni in eventi, emettendo ogni evento una sola volta.

    `sessions`: dict con almeno `session_id`, `status`, `last_activity` (epoch).
    `prev_state`: {"status": {sid: last_status}, "idle_alerted": {sid: bool}}.
    `cold_start`: se True registra lo stato corrente SENZA emettere eventi (evita il
    flood di notifiche storiche quando il watchdog parte su un registry già popolato).
    """
    events: list[tuple[str, dict[str, Any]]] = []
    prev_status = dict(prev_state.get("status", {}))
    idle_alerted = dict(prev_state.get("idle_alerted", {}))
    seen: set[str] = set()

    for s in sessions:
        sid = s.get("session_id")
        if not sid:
            continue
        seen.add(sid)
        status = s.get("status")
        before = prev_status.get(sid)

        if status == "Finished" and before != "Finished":
            events.append(("executed", s))
        elif status in STOPPED_STATES and before not in STOPPED_STATES:
            events.append(("stopped", s))
        elif status == "OnWorking" and before != "OnWorking":
            events.append(("started", s))

        if status == "OnWorking":
            last = s.get("last_activity") or 0
            if now - last > idle_threshold:
                if not idle_alerted.get(sid):
                    events.append(("idle", s))
                    idle_alerted[sid] = True
            else:
                idle_alerted[sid] = False
        else:
            idle_alerted.pop(sid, None)

        prev_status[sid] = status

    # Dimentica le sessioni sparite dal registry.
    for sid in list(prev_status):
        if sid not in seen:
            prev_status.pop(sid, None)
            idle_alerted.pop(sid, None)

    new_state = {"status": prev_status, "idle_alerted": idle_alerted}
    if cold_start:
        # Stato seminato ma nessuna notifica: solo i cambiamenti FUTURI generano eventi.
        return [], new_state
    return events, new_state


def _apply(template: str, mapping: dict[str, str]) -> str:
    """Sostituzione placeholder robusta (niente str.format: tollera graffe rogue)."""
    out = template
    for key in PLACEHOLDER_KEYS:
        out = out.replace("{" + key + "}", str(mapping.get(key, "")))
    return out


def _sign(text: str, provider: str) -> str:
    """Appone la firma del provider della sessione.

    La firma non sta nelle stringhe del pool né nel testo composto dall'agente:
    è questa funzione ad apporla, ed è l'unico punto in cui esiste. Per questo un
    provider mai visto prima firma col proprio nome senza che questo file cambi.
    """
    provider = (provider or "").strip()
    if not provider:
        return text
    return f"{text}\n— {provider}"


def render_message(
    event_type: str,
    agent: dict[str, Any],
    pool: dict[str, list[str]],
    *,
    name: str = "",
    now: float | None = None,
    rng: random.Random | None = None,
) -> str:
    """Il testo composto dall'agente se c'è, altrimenti il pool; poi la firma.

    Il testo composto va verbatim: l'agente l'ha scritto con la propria voce e
    nessun placeholder viene sostituito al suo interno.
    """
    composed = (agent.get("notify") or {}).get(event_type)
    if composed:
        return _sign(str(composed), agent.get("provider", ""))

    messages = pool.get(event_type) or ["{name}"]
    picker = rng or random
    template = picker.choice(messages)

    minutes = ""
    last = agent.get("last_activity") or 0
    if now and last:
        minutes = str(int((now - last) // 60))

    text = _apply(
        template,
        {
            "name": name or "capo",
            "session_id": agent.get("session_id", ""),
            "provider": agent.get("provider", ""),
            "working_on": agent.get("working_on", ""),
            "minutes": minutes,
        },
    )
    return _sign(text, agent.get("provider", ""))


def load_pool(notifier_dir: str) -> dict[str, list[str]]:
    """Preferisce il pool locale (personalizzato, gitignored), altrimenti il default."""
    local = os.path.join(notifier_dir, "messages.local.json")
    default = os.path.join(notifier_dir, "messages.default.json")
    path = local if os.path.exists(local) else default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read_sessions(home: Path) -> list[dict[str, Any]]:
    """Legge le sessioni da <home>/sessions/*.yaml; last_activity = mtime del file."""
    import yaml  # import locale: non serve ai test puri

    out: list[dict[str, Any]] = []
    sess_dir = home / "sessions"
    if not sess_dir.is_dir():
        return out
    for path in sess_dir.glob("*.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        data.setdefault("session_id", path.stem)
        data["last_activity"] = path.stat().st_mtime
        out.append(data)
    return out


WATCHDOG_LOCK_OWNER = "watchdog"


def _lock_manager() -> Any:
    """Importa `lock_manager` da scripts/ (presente anche nell'immagine)."""
    scripts = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
    )
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import lock_manager  # noqa: PLC0415 (import locale: non serve ai test puri)

    return lock_manager


def consume_notify(home: Path, session_id: str, event_type: str) -> bool:
    """Rimuove dal file di sessione il testo composto appena inviato.

    Una composizione produce esattamente un invio. Due cautele, entrambe
    necessarie:

    - il file appartiene all'agente, che può scriverlo in questo stesso istante:
      la riscrittura avviene sotto il lock di `lock_manager`, e se il lock è
      occupato si rinuncia — il messaggio è già partito, e riprovare al ciclo
      successivo costa meno che corrompere il file di un altro processo;
    - l'`mtime` è la sorgente di `last_activity`: se lo aggiornassimo, il
      watchdog leggerebbe la propria scrittura come attività dell'agente e
      sopprimerebbe l'evento `idle` che ha il compito di rilevare. Lo
      ripristiniamo con `os.utime()`.

    Ritorna True se la chiave è stata rimossa.
    """
    import yaml  # import locale: non serve ai test puri

    path = home / "sessions" / f"{session_id}.yaml"
    if not path.exists():
        return False

    try:
        lm = _lock_manager()
        result = lm.acquire_lock(str(path), WATCHDOG_LOCK_OWNER)
    except Exception as exc:  # lock_manager assente o inutilizzabile
        print(f"[watchdog] lock non disponibile per {session_id}: {exc}")
        return False
    if not result.get("locked"):
        print(f"[watchdog] {session_id} è in uso da {result.get('session_id')}: non consumo")
        return False

    try:
        stat = path.stat()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        notify = data.get("notify") or {}
        if event_type not in notify:
            return False
        notify.pop(event_type)
        if notify:
            data["notify"] = notify
        else:
            data.pop("notify", None)
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True,
                           default_flow_style=False),
            encoding="utf-8",
        )
        os.utime(path, (stat.st_atime, stat.st_mtime))
        return True
    except Exception as exc:  # best-effort: un ciclo non deve morire qui
        print(f"[watchdog] consumo fallito per {session_id}: {exc}")
        return False
    finally:
        try:
            lm.release_lock(str(path), WATCHDOG_LOCK_OWNER)
        except Exception:
            pass


def deliver_events(
    events: list[tuple[str, dict[str, Any]]],
    home: Path,
    pool: dict[str, list[str]],
    *,
    name: str = "",
    recipient: str = "",
    now: float | None = None,
) -> None:
    """Rende e spedisce gli eventi di un ciclo, consumando i testi composti.

    Estratta dal loop perché la regola che conta — si consuma **solo** dopo un
    invio riuscito — dentro un `while True` non sarebbe verificabile.
    """
    for etype, agent in events:
        text = render_message(etype, agent, pool, name=name, now=now)
        composed = bool((agent.get("notify") or {}).get(etype))
        if not recipient:
            print(f"[watchdog] (nessun WA_RECIPIENT) {etype}: {text}")
            continue
        try:
            wa_client.send_text(text, recipient)
        except Exception as exc:
            # Il testo composto resta nel file: riparte al ciclo successivo.
            print(f"[watchdog] invio fallito ({etype}): {exc}")
            continue
        origin = "composto" if composed else "pool"
        print(f"[watchdog] inviato {etype} ({origin}) -> {agent.get('session_id')}")
        if composed:
            consume_notify(home, str(agent.get("session_id", "")), etype)


def _load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": {}, "idle_alerted": {}}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(state), encoding="utf-8")
    except Exception as exc:  # best-effort
        print(f"[watchdog] impossibile salvare lo stato: {exc}")


def main() -> None:
    home = Path(os.environ.get("AGENT_REGISTRY_HOME", "/data"))
    notifier_dir = os.path.dirname(os.path.abspath(__file__))
    interval = int(os.environ.get("WATCHDOG_INTERVAL", "60"))
    idle_threshold = int(os.environ.get("IDLE_THRESHOLD", "3600"))
    name = os.environ.get("WA_NAME", "")
    recipient = os.environ.get("WA_RECIPIENT", "")

    pool = load_pool(notifier_dir)
    state_path = home / ".watchdog-state.json"
    cold = not state_path.exists()
    state = _load_state(state_path)
    print(f"[watchdog] avvio (home={home}, idle>{idle_threshold}s, ogni {interval}s)")
    if cold:
        print("[watchdog] avvio a freddo: semino lo stato corrente senza notifiche storiche")

    first_cycle = cold
    while True:
        sessions = _read_sessions(home)
        now = time.time()
        events, state = classify_events(
            sessions, state, now, idle_threshold, cold_start=first_cycle
        )
        first_cycle = False
        deliver_events(events, home, pool, name=name, recipient=recipient, now=now)
        _save_state(state_path, state)
        time.sleep(interval)


if __name__ == "__main__":
    main()
