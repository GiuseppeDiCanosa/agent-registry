#!/usr/bin/env python3
"""Gestore del git-sync multi-macchina per la home agent-registry.

La home `~/.agent-registry/` (o `AGENT_REGISTRY_HOME`) può essere un
repository git con remote privato: dopo ogni scrittura il registry schedula
un sync in background (`add` → `commit` → `pull --rebase` → `push`).

Principi:
- Best-effort totale: nessuna operazione di registry fallisce per colpa del
  sync. Errori di rete/push rimandano il push al sync successivo.
- Debounce (~2s) ed esecuzione in thread daemon: il chiamante non blocca mai.
- Lock in-process (threading.Lock) per serializzare i sync.
- Lo stato del sync è persistito in `sync-status.json` nella home (gitignored,
  leggibile da `status` e dalla dashboard).

Modalità read-only: `fetch_registry_via_api()` scarica `registry.md` via
GitHub Contents API (solo stdlib `urllib`) per macchine senza repo clonato.

CLI:
    sync_manager.py init --git-remote <url>
    sync_manager.py sync
    sync_manager.py status
    sync_manager.py fetch-remote <owner/repo> [--branch main]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GITIGNORE_ENTRIES = ("locks/", "wiki.db", "*.tmp", "__pycache__/", "sync-status.json")
SYNC_STATUS_FILE = "sync-status.json"
DEBOUNCE_SECONDS = 2.0
MAX_RETRIES = 3
GIT_TIMEOUT = 60

_sync_lock = threading.Lock()  # serializza i sync effettivi
_schedule_lock = threading.Lock()  # protegge il timer pendente
_pending_timer: threading.Timer | None = None


def _registry_manager() -> Any:
    """Import lazy di registry_manager (evita import circolari)."""
    scripts_dir = str(Path(__file__).parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import registry_manager

    return registry_manager


def _default_home() -> Path:
    return _registry_manager().get_registry_home()


def _git(home: Path, *args: str, check: bool = False, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Esegue un comando git nella home, senza mai sollevare se check=False."""
    result = subprocess.run(
        ["git", "-C", str(home), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if check and result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} fallito: {output}")
    return result


def _current_branch(home: Path) -> str:
    """Branch corrente del repo; 'main' come fallback."""
    result = _git(home, "symbolic-ref", "--short", "HEAD", timeout=15)
    branch = result.stdout.strip()
    return branch or "main"


def is_git_enabled(home: Path | None = None) -> bool:
    """True se la home è un repository git con remote 'origin' configurato."""
    home = Path(home) if home is not None else _default_home()
    if not (home / ".git").exists():
        return False
    result = _git(home, "remote", "get-url", "origin", timeout=15)
    return result.returncode == 0 and bool(result.stdout.strip())


def _ensure_gitignore(home: Path) -> None:
    """Crea/aggiorna .gitignore con le entry machine-local."""
    path = home / ".gitignore"
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    present = {line.strip() for line in lines}
    missing = [entry for entry in GITIGNORE_ENTRIES if entry not in present]
    if missing:
        with open(path, "a", encoding="utf-8") as f:
            if lines and lines[-1].strip():
                f.write("\n")
            f.write("\n".join(missing) + "\n")


def _ensure_git_identity(home: Path) -> None:
    """Imposta user.name/user.email locali al repo se mancanti (serve per committare)."""
    if not _git(home, "config", "user.name", timeout=15).stdout.strip():
        _git(home, "config", "user.name", "agent-registry", timeout=15)
    if not _git(home, "config", "user.email", timeout=15).stdout.strip():
        _git(home, "config", "user.email", "agent-registry@localhost", timeout=15)


def init_git_sync(remote_url: str, home: Path | None = None) -> Path:
    """Inizializza la home come repo git con remote 'origin'.

    Se la home non esiste la crea (struttura standard + registry.md vuoto);
    se non è un repo esegue `git init`, scrive `.gitignore` e il primo commit.
    Se è già un repo configura soltanto il remote.
    """
    rm = _registry_manager()
    home = Path(home) if home is not None else rm.get_registry_home()
    rm._ensure_structure(home)
    if not (home / "registry.md").exists():
        rm._render_view(home)

    if not (home / ".git").exists():
        _git(home, "init", check=True)
        _ensure_gitignore(home)
        _ensure_git_identity(home)
        if _git(home, "rev-parse", "--verify", "HEAD", timeout=15).returncode != 0:
            _git(home, "add", "-A", check=True)
            _git(home, "commit", "-m", "chore: init agent-registry home", check=True)

    remotes = _git(home, "remote", timeout=15).stdout.split()
    if "origin" in remotes:
        _git(home, "remote", "set-url", "origin", remote_url, check=True)
    else:
        _git(home, "remote", "add", "origin", remote_url, check=True)
    return home


# Pattern per la classificazione degli errori di `git ls-remote` (stderr).
_AUTH_PATTERNS = (
    "permission denied",
    "authentication failed",
    "could not read username",
    "could not read password",
    "terminal prompts disabled",
    "invalid username or password",
    "access denied",
)
_UNREACHABLE_PATTERNS = (
    "could not resolve hostname",
    "name or service not known",
    "failed to connect",
    "connection refused",
    "connection timed out",
    "operation timed out",
    "network is unreachable",
    "no route to host",
)
_MALFORMED_PATTERNS = (
    "invalid url",
    "invalid protocol",
    "bad url",
    "empty url",
    "no url specified",
    "protocol not supported",
    "is not supported",
    "does not appear to be a git repository",
    "invalid path",
)


def _classify_lsremote_error(stderr: str, returncode: int) -> tuple[str, str]:
    """Classifica l'errore di `git ls-remote` in (kind, dettaglio).

    kind ∈ {"malformed_url", "auth_failed", "unreachable", "unknown"}.
    Matching multi-pattern su stderr (case-insensitive); per "unknown" il
    dettaglio allega lo stderr grezzo per la diagnosi.
    """
    text = (stderr or "").lower()
    if any(p in text for p in _AUTH_PATTERNS):
        kind = "auth_failed"
        detail = "autenticazione fallita verso il remote (chiave SSH o token HTTPS)"
    elif any(p in text for p in _MALFORMED_PATTERNS):
        kind = "malformed_url"
        detail = "URL del remote malformato o non riconosciuto da git"
    elif any(p in text for p in _UNREACHABLE_PATTERNS):
        kind = "unreachable"
        detail = "remote non raggiungibile (rete o host non disponibile)"
    else:
        kind = "unknown"
        raw = (stderr or "").strip()
        detail = f"errore git non classificato (exit {returncode}): {raw or 'nessun output'}"
    return kind, detail


def _is_plausible_git_url(url: str) -> bool:
    """Verifica sintattica minima: scp-like (`user@host:path`) o `scheme://...`."""
    if not url or any(c.isspace() for c in url):
        return False
    if "://" in url:
        scheme, rest = url.split("://", 1)
        return bool(scheme) and bool(rest.strip("/"))
    return ":" in url and bool(url.split(":", 1)[0])


def validate_remote(url: str) -> dict[str, Any]:
    """Pre-valida il remote con `git ls-remote` (read-only, nessun side-effect).

    Ritorna un dict:
      {"ok": True, "state": "empty" | "populated"}
      {"ok": False, "error_kind": kind, "message": dettaglio}
    kind proviene da `_classify_lsremote_error`; timeout e git assente sono
    classificati rispettivamente come "unreachable" e "unknown".
    """
    url = (url or "").strip()
    if not _is_plausible_git_url(url):
        return {
            "ok": False,
            "error_kind": "malformed_url",
            "message": "URL del remote malformato (atteso scp-like user@host:path o scheme://...)",
        }
    try:
        result = subprocess.run(
            ["git", "ls-remote", url],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_kind": "unreachable",
            "message": "remote non raggiungibile: timeout di 30s su git ls-remote",
        }
    except OSError as exc:
        return {
            "ok": False,
            "error_kind": "unknown",
            "message": f"impossibile eseguire git ls-remote: {exc}",
        }
    if result.returncode == 0:
        return {"ok": True, "state": "populated" if result.stdout.strip() else "empty"}
    kind, detail = _classify_lsremote_error(result.stderr or result.stdout or "", result.returncode)
    return {"ok": False, "error_kind": kind, "message": detail}


def _home_has_user_data(home: Path) -> bool:
    """True se la home contiene dati utente: `sessions/*.yaml`, `wiki/*.md`
    o `contexts/*` con contenuto.

    Guard conservativo per la scelta del ramo di setup: in caso di errore di
    lettura si assume che ci siano dati utente (mai cancellazione silenziosa).
    """
    home = Path(home)
    try:
        for pattern in ("sessions/*.yaml", "wiki/*.md"):
            for path in home.glob(pattern):
                if path.is_file() and path.stat().st_size > 0:
                    return True
        contexts_dir = home / "contexts"
        if contexts_dir.is_dir():
            for path in contexts_dir.rglob("*"):
                if path.is_file() and path.stat().st_size > 0:
                    return True
    except OSError:
        return True  # in dubbio: dati utente presenti
    return False


def _status_path(home: Path) -> Path:
    return home / SYNC_STATUS_FILE


def _read_status(home: Path) -> dict[str, Any]:
    path = _status_path(home)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _write_status(home: Path, status: dict[str, Any]) -> None:
    try:
        _status_path(home).write_text(
            json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def get_sync_status(home: Path | None = None) -> dict[str, Any]:
    """Stato del sync: enabled, last_sync_at, last_error, pending."""
    home = Path(home) if home is not None else _default_home()
    status = {
        "enabled": is_git_enabled(home),
        "last_sync_at": None,
        "last_error": None,
        "pending": False,
    }
    status.update({k: v for k, v in _read_status(home).items() if k in status})
    return status


def _resolve_conflict(home: Path) -> None:
    """Abort del rebase e rigenerazione di registry.md dai sessions/*.yaml."""
    _git(home, "rebase", "--abort", timeout=30)
    try:
        _registry_manager()._render_view(home)
    except Exception:
        pass  # la vista verrà rigenerata alla prossima scrittura


def sync_now(home: Path | None = None, message: str = "update") -> dict[str, Any]:
    """Esegue un sync sincrono: add → commit → pull --rebase → push.

    In caso di conflitto di merge abortisce il rebase, rigenera registry.md
    e riprova (max MAX_RETRIES tentativi). Errori di rete/push non sollevano:
    vengono registrati in sync-status.json e il push è rimandato al sync
    successivo. Restituisce lo stato di sync aggiornato.
    """
    home = Path(home) if home is not None else _default_home()
    previous = get_sync_status(home)
    if not previous["enabled"]:
        return previous

    last_error: str | None = None
    synced = False
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _git(home, "add", "-A", check=True)
            if _git(home, "diff", "--cached", "--quiet", timeout=30).returncode != 0:
                _git(home, "commit", "-m", f"auto: {message}", check=True)
            branch = _current_branch(home)
            pull = _git(home, "pull", "--rebase", "origin", branch)
            if pull.returncode != 0:
                output = f"{pull.stdout or ''}\n{pull.stderr or ''}"
                if "couldn't find remote ref" in output or "could not find remote ref" in output:
                    pass  # primo push: il branch remoto non esiste ancora
                elif "CONFLICT" in output or "could not apply" in output:
                    _resolve_conflict(home)
                    last_error = f"conflitto di merge (tentativo {attempt}/{MAX_RETRIES})"
                    continue
                else:
                    _git(home, "rebase", "--abort", timeout=30)
                    raise RuntimeError(f"git pull fallito: {output.strip()}")
            _git(home, "push", "-u", "origin", branch, check=True)
            synced = True
            last_error = None
            break
        except Exception as exc:  # rete, push rifiutato, ecc. → rimandato
            last_error = str(exc)
            break

    status = {
        "enabled": True,
        "last_sync_at": datetime.now(timezone.utc).isoformat() if synced else previous["last_sync_at"],
        "last_error": last_error,
        "pending": not synced,
    }
    _write_status(home, status)
    if last_error:
        print(f"[agent-registry] sync fallito: {last_error}", file=sys.stderr)
    return status


def _sync_worker(home: Path, message: str) -> None:
    """Esegue il sync nel thread daemon; non propaga mai eccezioni."""
    with _sync_lock:
        try:
            sync_now(home, message)
        except Exception as exc:
            print(f"[agent-registry] sync fallito: {exc}", file=sys.stderr)


def schedule_sync(home: Path | None = None, message: str = "update") -> threading.Timer | None:
    """Schedula un sync in background con debounce (DEBOUNCE_SECONDS).

    Chiamate ravvicinate annullano il sync pendente e ne schedulano uno solo.
    Restituisce il timer (thread daemon) o None se il git-sync non è abilitato.
    """
    global _pending_timer
    home = Path(home) if home is not None else _default_home()
    if not is_git_enabled(home):
        return None
    with _schedule_lock:
        if _pending_timer is not None:
            _pending_timer.cancel()
        timer = threading.Timer(DEBOUNCE_SECONDS, _sync_worker, args=(home, message))
        timer.daemon = True
        _pending_timer = timer
        timer.start()
        return timer


def fetch_registry_via_api(token: str, repo: str, branch: str = "main") -> str:
    """Scarica il contenuto di registry.md via GitHub Contents API (read-only).

    `repo` nel formato "owner/repo". Usa solo la stdlib (urllib); solleva
    urllib.error.HTTPError in caso di risposta non 2xx.
    """
    url = f"https://api.github.com/repos/{repo}/contents/registry.md?ref={branch}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.raw",
            "Authorization": f"Bearer {token}",
            "User-Agent": "agent-registry-sync",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Git-sync per la home agent-registry.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Inizializza la home come repo git con remote.")
    p_init.add_argument("--git-remote", required=True, help="URL del remote privato (origin).")

    sub.add_parser("sync", help="Forza un sync sincrono (add/commit/pull/push).")
    sub.add_parser("status", help="Mostra lo stato del sync (JSON).")

    p_fetch = sub.add_parser("fetch-remote", help="Legge registry.md remoto via GitHub API.")
    p_fetch.add_argument("repo", help="Repository nel formato owner/repo.")
    p_fetch.add_argument("--branch", default="main", help="Branch da leggere (default: main).")

    args = parser.parse_args(argv)

    if args.command == "init":
        home = init_git_sync(args.git_remote)
        print(f"Git-sync inizializzato in {home} (remote: {args.git_remote})")
        return 0
    if args.command == "sync":
        status = sync_now(message="manual sync")
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return 0 if status["last_error"] is None else 1
    if args.command == "status":
        print(json.dumps(get_sync_status(), indent=2, ensure_ascii=False))
        return 0
    if args.command == "fetch-remote":
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            print("Errore: imposta GITHUB_TOKEN o GH_TOKEN.", file=sys.stderr)
            return 1
        print(fetch_registry_via_api(token, args.repo, branch=args.branch))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
