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


def render_message(
    event_type: str,
    agent: dict[str, Any],
    pool: dict[str, list[str]],
    *,
    name: str = "",
    now: float | None = None,
    rng: random.Random | None = None,
) -> str:
    """Sceglie a caso un messaggio dal pool dell'evento e sostituisce i placeholder."""
    messages = pool.get(event_type) or ["{name}"]
    picker = rng or random
    template = picker.choice(messages)

    minutes = ""
    last = agent.get("last_activity") or 0
    if now and last:
        minutes = str(int((now - last) // 60))

    return _apply(
        template,
        {
            "name": name or "capo",
            "session_id": agent.get("session_id", ""),
            "provider": agent.get("provider", ""),
            "working_on": agent.get("working_on", ""),
            "minutes": minutes,
        },
    )


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
        for etype, agent in events:
            text = render_message(etype, agent, pool, name=name, now=now)
            if recipient:
                try:
                    wa_client.send_text(text, recipient)
                    print(f"[watchdog] inviato {etype} -> {agent.get('session_id')}")
                except Exception as exc:
                    print(f"[watchdog] invio fallito ({etype}): {exc}")
            else:
                print(f"[watchdog] (nessun WA_RECIPIENT) {etype}: {text}")
        _save_state(state_path, state)
        time.sleep(interval)


if __name__ == "__main__":
    main()
