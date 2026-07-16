"""Infrastruttura per i test cross-process di agent-registry.

Il modello d'esecuzione reale della skill è: un agente lancia un comando bash
one-shot, il processo acquisisce/rilascia e **muore**. Ogni garanzia di mutua
esclusione va quindi verificata fra processi che terminano davvero.

Testare in-process è ciò che ha reso verde la suite 0.1.0 su codice rotto: la
mutua esclusione era garantita da un dict in memoria, mai dal filesystem.
Qui ogni operazione gira in un interprete separato che esce prima
dell'asserzione.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = str(Path(__file__).parent.parent / "scripts")

# Preambolo eseguito in ogni processo figlio: rende importabili i manager.
_PREAMBLE = """
import json, os, sys, time
sys.path.insert(0, {scripts!r})
"""

# Corpo che invoca una funzione del modulo e stampa il risultato come JSON su
# una riga marcata, così stdout sporco (warning, print altrui) non rompe il parse.
_CALL = """
import {module} as m
{barrier}
try:
    _res = getattr(m, {func!r})(*{args!r}, **{kwargs!r})
    print("__RESULT__" + json.dumps(_res, default=str))
except Exception as e:
    print("__RESULT__" + json.dumps({{"__exception__": type(e).__name__, "message": str(e)}}))
"""

# Attesa attiva fino a un istante di wall-clock condiviso: senza questo i
# processi partono scaglionati dallo startup di Python (~50ms) e si
# serializzano da soli, nascondendo proprio le race che vogliamo osservare.
_BARRIER = """
_start_at = {start_at!r}
while time.time() < _start_at:
    pass
"""


def _build_source(module, func, args, kwargs, start_at=None):
    barrier = _BARRIER.format(start_at=start_at) if start_at is not None else ""
    return _PREAMBLE.format(scripts=SCRIPTS_DIR) + _CALL.format(
        module=module, func=func, args=args, kwargs=kwargs, barrier=barrier
    )


# Sentinella: `None` è un valore di ritorno legittimo (find_agent su id
# assente), quindi non può segnalare anche "il figlio non ha prodotto nulla".
NO_RESULT = object()


def _parse_result(stdout: str):
    for line in stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__") :])
    return NO_RESULT


class ProcessRunner:
    """Esegue chiamate ai manager in processi separati che terminano."""

    def __init__(self, env: dict[str, str]):
        self.env = env

    def call(self, module: str, func: str, *args, cwd=None, **kwargs):
        """Chiama `module.func(*args, **kwargs)` in un processo che poi muore.

        `cwd` esegue il figlio da un'altra directory di lavoro, per verificare
        che l'identità del lock non dipenda da dove è stato invocato.
        Restituisce il valore di ritorno deserializzato. Il processo è
        garantito terminato quando questo metodo ritorna.
        """
        src = _build_source(module, func, list(args), kwargs)
        proc = subprocess.run(
            [sys.executable, "-c", src],
            env=self.env,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        result = _parse_result(proc.stdout)
        if result is NO_RESULT:
            raise AssertionError(
                f"Nessun risultato dal processo figlio.\n"
                f"exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
            )
        return result

    def race(self, n: int, module: str, func: str, *args, **kwargs) -> list:
        """Lancia n processi che invocano la stessa funzione nello stesso istante.

        Tutti attendono una barriera di wall-clock condivisa prima di agire,
        così la contesa è reale e non un artefatto dello scaglionamento.
        Restituisce la lista dei risultati.
        """
        start_at = time.time() + 1.0
        procs = []
        for _ in range(n):
            src = _build_source(module, func, list(args), kwargs, start_at=start_at)
            procs.append(
                subprocess.Popen(
                    [sys.executable, "-c", src],
                    env=self.env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        results = []
        for p in procs:
            out, err = p.communicate(timeout=30)
            res = _parse_result(out)
            if res is NO_RESULT:
                raise AssertionError(
                    f"Nessun risultato da un processo della race.\n"
                    f"exit={p.returncode}\nstdout={out}\nstderr={err}"
                )
            results.append(res)
        return results

    def race_varied(self, calls: list[tuple]) -> list:
        """Come race(), ma ogni processo riceve argomenti propri.

        `calls` è una lista di (module, func, args, kwargs).
        """
        start_at = time.time() + 1.0
        procs = []
        for module, func, args, kwargs in calls:
            src = _build_source(module, func, list(args), kwargs, start_at=start_at)
            procs.append(
                subprocess.Popen(
                    [sys.executable, "-c", src],
                    env=self.env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        results = []
        for p in procs:
            out, err = p.communicate(timeout=30)
            res = _parse_result(out)
            if res is NO_RESULT:
                raise AssertionError(
                    f"Nessun risultato da un processo della race.\n"
                    f"exit={p.returncode}\nstdout={out}\nstderr={err}"
                )
            results.append(res)
        return results

    def cli(self, script: str, *argv: str) -> subprocess.CompletedProcess:
        """Esegue un manager via CLI, come farebbe un agente da bash."""
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, script), *argv],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )


@pytest.fixture
def fake_home(tmp_path):
    """HOME finta, così i default `~/Desktop/...` non toccano il Desktop vero.

    Rete di sicurezza, non un dettaglio: un'implementazione che ignorasse
    l'override d'ambiente ricadrebbe sul default e scriverebbe nella home
    reale dell'utente. Con HOME reindirizzata, il default finisce comunque
    dentro tmp_path e il test può verificarlo invece di sporcare il sistema.
    """
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    return home


@pytest.fixture
def isolated_env(tmp_path, fake_home):
    """Ambiente con lock dir e registry isolati in tmp_path.

    Isolare via ambiente (non via monkeypatch del modulo) è l'unico modo che
    funziona quando il codice sotto test gira in un altro processo — ed è
    proprio ciò che il design 0.1.0 impediva: con `LOCK_DIR` risolta all'import
    l'unico isolamento possibile era il monkeypatch in-process, che a sua volta
    obbligava a test in-process. Il design non testabile ha prodotto i test ciechi.
    """
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env["AGENT_REGISTRY_LOCK_DIR"] = str(tmp_path / "locks")
    env["AGENT_REGISTRY_PATH"] = str(tmp_path / "registry.md")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@pytest.fixture
def runner(isolated_env):
    return ProcessRunner(isolated_env)


@pytest.fixture
def target_file(tmp_path):
    """Un file bersaglio realistico da lockare."""
    f = tmp_path / "project" / "src" / "auth.py"
    f.parent.mkdir(parents=True)
    f.write_text("# codice conteso\n")
    return f
