"""Verifica statica della configurazione Docker Compose (capability container-deployment).

Parsa i file direttamente (senza daemon Docker), così gira in CI senza dipendenze.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PY_SERVICES = ("db", "dashboard", "code", "watchdog")
ALL_SERVICES = PY_SERVICES + ("wa-gateway",)


def _compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _env(service: dict) -> dict:
    return service.get("environment", {}) or {}


def test_services_share_data_home():
    services = _compose()["services"]
    for name in PY_SERVICES:
        assert name in services, f"servizio mancante: {name}"
        svc = services[name]
        assert _env(svc).get("AGENT_REGISTRY_HOME") == "/data"
        mounts = svc.get("volumes", [])
        assert any(str(m).endswith(":/data") or ":/data:" in str(m) for m in mounts), name
        assert "AGENT_REGISTRY_PATH" not in _env(svc), f"{name} usa la variabile deprecata"


def test_data_source_parameterized_default_isolated():
    services = _compose()["services"]
    for name in PY_SERVICES:
        mounts = [str(m) for m in services[name].get("volumes", [])]
        data_mount = next(m for m in mounts if m.endswith(":/data"))
        assert "AGENT_REGISTRY_DATA_SOURCE" in data_mount, name
        assert "agent-registry-data" in data_mount, name  # default isolato


def test_no_deprecated_path_anywhere():
    raw = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "AGENT_REGISTRY_PATH" not in raw


def test_dashboard_always_on():
    svc = _compose()["services"]["dashboard"]
    cmd = " ".join(svc["command"]) if isinstance(svc["command"], list) else str(svc["command"])
    assert "0.0.0.0" in cmd and "8765" in cmd
    assert svc.get("restart") == "unless-stopped"
    hc = svc.get("healthcheck", {})
    assert any("/api/sync" in str(x) for x in hc.get("test", []))


def test_orbstack_domain_per_service():
    services = _compose()["services"]
    for name in ALL_SERVICES:
        labels = services[name].get("labels", {})
        domain = labels.get("dev.orbstack.domains")
        assert domain, f"{name} senza dominio OrbStack"
        expected_sub = "wa." if name == "wa-gateway" else f"{name}."
        assert domain.startswith(expected_sub)
        assert domain.endswith("agent-registry.orb.local")


def test_python_services_share_image():
    services = _compose()["services"]
    images = {services[n].get("image") for n in PY_SERVICES}
    assert images == {"agent-registry:local"}, images


def test_dockerfile_installs_both_requirements():
    df = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "scripts/requirements.txt" in df
    assert "scripts/webapp/requirements.txt" in df


def test_dockerfile_installs_ssh_client():
    df = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "openssh-client" in df


def test_db_mounts_git_credentials():
    svc = _compose()["services"]["db"]
    mounts = [str(m) for m in svc.get("volumes", [])]
    assert any(".ssh" in m and m.endswith(":ro") for m in mounts), mounts
    assert any(".gitconfig" in m and m.endswith(":ro") for m in mounts), mounts


def test_db_runs_sync_loop():
    svc = _compose()["services"]["db"]
    cmd = " ".join(svc["command"]) if isinstance(svc["command"], list) else str(svc["command"])
    assert "sync-loop.sh" in cmd
    loop = (ROOT / "docker" / "sync-loop.sh").read_text(encoding="utf-8")
    assert "sync_manager.py sync" in loop


def test_no_secrets_committed():
    raw = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    raw += (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    # nessuna API key reale né numero di telefono in chiaro
    assert not re.search(r"sk-[A-Za-z0-9]{16,}", raw)
    assert not re.search(r"\b\d{10,15}\b", raw)
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
