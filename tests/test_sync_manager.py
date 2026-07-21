"""Test per sync_manager.py (git-sync multi-macchina)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import registry_manager as rm
import sync_manager as sm


@pytest.fixture(autouse=True)
def _no_real_legacy(monkeypatch, tmp_path):
    """Impedisce che i test tocchino un eventuale registry legacy reale su Desktop."""
    monkeypatch.setattr(
        rm, "_legacy_registry_path", lambda: tmp_path / "no-legacy" / "registry.md"
    )


@pytest.fixture(autouse=True)
def _fast_debounce(monkeypatch):
    """Riduce il debounce per rendere i test rapidi."""
    monkeypatch.setattr(sm, "DEBOUNCE_SECONDS", 0.05)


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Home del registry in una directory temporanea (via AGENT_REGISTRY_HOME)."""
    home = tmp_path / "registry-home"
    monkeypatch.setenv("AGENT_REGISTRY_HOME", str(home))
    monkeypatch.delenv("AGENT_REGISTRY_PATH", raising=False)
    return home


@pytest.fixture
def bare_remote(tmp_path):
    """Remote finto: repository git bare in una directory temporanea."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        capture_output=True,
    )
    return remote


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _join_pending(timeout: float = 60.0) -> None:
    """Attende il thread del sync schedulato."""
    timer = sm._pending_timer
    assert timer is not None, "nessun sync schedulato"
    timer.join(timeout=timeout)
    assert not timer.is_alive(), "il thread di sync non è terminato in tempo"


# --- init / is_git_enabled ---


def test_init_creates_repo_commit_and_remote(tmp_home, bare_remote):
    home = sm.init_git_sync(f"file://{bare_remote}", tmp_home)

    assert (home / ".git").is_dir()
    assert _git(home, "remote", "get-url", "origin").stdout.strip() == f"file://{bare_remote}"
    # primo commit presente
    assert _git(home, "rev-parse", "--verify", "HEAD").returncode == 0
    # registry.md tracciato nel primo commit
    tracked = _git(home, "ls-tree", "-r", "--name-only", "HEAD").stdout
    assert "registry.md" in tracked
    assert sm.is_git_enabled(home) is True


def test_init_writes_gitignore(tmp_home, bare_remote):
    home = sm.init_git_sync(f"file://{bare_remote}", tmp_home)
    content = (home / ".gitignore").read_text(encoding="utf-8")
    for entry in ("locks/", "wiki.db", "*.tmp", "__pycache__/", "sync-status.json"):
        assert entry in content


def test_init_existing_repo_configures_only_remote(tmp_home, bare_remote):
    # Repo già esistente senza remote: init configura solo il remote.
    tmp_home.mkdir(parents=True)
    _git(tmp_home, "init")
    home = sm.init_git_sync(f"file://{bare_remote}", tmp_home)
    assert _git(home, "remote", "get-url", "origin").stdout.strip() == f"file://{bare_remote}"
    assert sm.is_git_enabled(home) is True


def test_is_git_enabled_false_without_repo_or_remote(tmp_home, bare_remote):
    tmp_home.mkdir(parents=True)
    assert sm.is_git_enabled(tmp_home) is False
    _git(tmp_home, "init")
    assert sm.is_git_enabled(tmp_home) is False  # repo senza remote


# --- schedule_sync end-to-end ---


def test_schedule_sync_pushes_to_remote(tmp_home, bare_remote):
    home = sm.init_git_sync(f"file://{bare_remote}", tmp_home)
    (home / "contexts" / "note.md").write_text("nota di test", encoding="utf-8")
    timer = sm.schedule_sync(home, "test sync")
    assert timer is not None
    timer.join(timeout=60)
    assert not timer.is_alive()

    log = _git(bare_remote, "log", "--format=%s", "--all").stdout
    assert "auto: test sync" in log
    pushed = _git(bare_remote, "ls-tree", "-r", "--name-only", "HEAD").stdout
    assert "contexts/note.md" in pushed

    status = sm.get_sync_status(home)
    assert status["enabled"] is True
    assert status["last_sync_at"] is not None
    assert status["last_error"] is None
    assert status["pending"] is False


def test_registry_write_triggers_sync(tmp_home, bare_remote):
    """Integrazione (2.4): una scrittura del registry schedula il sync."""
    sm.init_git_sync(f"file://{bare_remote}", tmp_home)
    rm.register_session("sid-sync", "Kimi", "2.7", "Test sync")
    _join_pending()

    pushed = _git(bare_remote, "ls-tree", "-r", "--name-only", "HEAD").stdout
    assert "sessions/sid-sync.yaml" in pushed
    content = _git(bare_remote, "show", "HEAD:sessions/sid-sync.yaml").stdout
    assert "sid-sync" in content


def test_update_triggers_sync(tmp_home, bare_remote):
    sm.init_git_sync(f"file://{bare_remote}", tmp_home)
    rm.register_session("sid-upd", "Kimi", "2.7", "Prima")
    _join_pending()
    rm.update_session("sid-upd", working_on="Dopo")
    _join_pending()

    content = _git(bare_remote, "show", "HEAD:sessions/sid-upd.yaml").stdout
    assert "Dopo" in content


def test_gitignore_respected(tmp_home, bare_remote):
    home = sm.init_git_sync(f"file://{bare_remote}", tmp_home)
    (home / "locks").mkdir(exist_ok=True)
    (home / "locks" / "a.lock").write_text("lock", encoding="utf-8")
    (home / "wiki.db").write_bytes(b"sqlite")
    rm.register_session("sid-ign", "Kimi", "2.7", "Ignore me")
    _join_pending()

    tracked = _git(bare_remote, "ls-tree", "-r", "--name-only", "HEAD").stdout
    assert "locks/" not in tracked
    assert "wiki.db" not in tracked
    assert "sync-status.json" not in tracked
    assert "sessions/sid-ign.yaml" in tracked


# --- offline / best-effort ---


def test_offline_sync_does_not_raise_and_records_error(tmp_home, tmp_path):
    # Remote che sparisce dopo il setup: nessuna eccezione, last_error valorizzato.
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    sm.init_git_sync(f"file://{bare}", tmp_home)
    shutil.rmtree(bare)
    rm.register_session("sid-off", "Kimi", "2.7", "Offline")  # non deve sollevare
    _join_pending()

    status = sm.get_sync_status(tmp_home)
    assert status["enabled"] is True
    assert status["last_error"] is not None
    assert status["pending"] is True
    # la sessione locale esiste comunque
    assert rm.find_agent("sid-off") is not None


def test_get_sync_status_defaults_on_plain_home(tmp_home):
    tmp_home.mkdir(parents=True)
    status = sm.get_sync_status(tmp_home)
    assert status == {
        "enabled": False,
        "last_sync_at": None,
        "last_error": None,
        "pending": False,
    }


def test_registry_write_without_git_never_fails(tmp_home):
    # Home non git: le operazioni del registry funzionano senza sync.
    timer_before = sm._pending_timer
    agent = rm.register_session("sid-plain", "Kimi", "2.7", "No git")
    assert agent["session_id"] == "sid-plain"
    assert sm._pending_timer is timer_before  # nessun nuovo sync schedulato


# --- GitHub API read-only ---


def test_fetch_registry_via_api(monkeypatch):
    calls: dict = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"--- registry remoto ---"

    def fake_urlopen(request, timeout=None):
        calls["request"] = request
        return FakeResponse()

    monkeypatch.setattr(sm.urllib.request, "urlopen", fake_urlopen)
    content = sm.fetch_registry_via_api("tok123", "owner/repo", branch="dev")

    assert content == "--- registry remoto ---"
    request = calls["request"]
    assert "repos/owner/repo/contents/registry.md" in request.full_url
    assert "ref=dev" in request.full_url
    assert request.headers["Authorization"] == "Bearer tok123"
    assert request.headers["Accept"] == "application/vnd.github.raw"


def test_cli_fetch_remote(monkeypatch, capsys):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"contenuto remoto"

    monkeypatch.setattr(sm.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse())
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    rc = sm.main(["fetch-remote", "owner/repo"])
    assert rc == 0
    assert "contenuto remoto" in capsys.readouterr().out


def test_cli_fetch_remote_without_token(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    rc = sm.main(["fetch-remote", "owner/repo"])
    assert rc == 1
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_cli_status(tmp_home, capsys):
    tmp_home.mkdir(parents=True)
    rc = sm.main(["status"])
    assert rc == 0
    status = json.loads(capsys.readouterr().out)
    assert status["enabled"] is False


# --- setup_git_sync: ramo (a) init con remote vuoto ---


def test_setup_init_branch_empty_remote(tmp_home, bare_remote):
    """Ramo (a): remote bare `file://` vuoto → init + primo push riusciti."""
    result = sm.setup_git_sync(f"file://{bare_remote}", home=tmp_home)

    assert result["status"] == "ok"
    assert result["branch"] == "init"
    assert (tmp_home / ".git").is_dir()
    assert sm.is_git_enabled(tmp_home) is True

    # push riuscito: il remote contiene il primo commit con registry.md
    branch = _git(tmp_home, "symbolic-ref", "--short", "HEAD").stdout.strip()
    refs = _git(bare_remote, "show-ref").stdout
    assert f"refs/heads/{branch}" in refs
    tracked = _git(bare_remote, "ls-tree", "-r", "--name-only", branch).stdout
    assert "registry.md" in tracked


# --- _classify_lsremote_error (stderr realistici) ---

# stderr osservati da git reale (Linux/macOS, locale inglese).
SSH_DENIED_STDERR = (
    "git@github.com: Permission denied (publickey).\r\n"
    "fatal: Could not read from remote repository.\n\n"
    "Please make sure you have the correct access rights\n"
    "and the repository exists.\n"
)
HTTPS_AUTH_STDERR = (
    "remote: Invalid username or password.\n"
    "fatal: Authentication failed for 'https://github.com/owner/repo.git/'\n"
)
HTTPS_NO_PROMPT_STDERR = (
    "fatal: could not read Username for 'https://github.com': "
    "terminal prompts disabled\n"
)
UNRESOLVABLE_HOST_STDERR = (
    "ssh: Could not resolve hostname githb.com: nodename nor servname provided, "
    "or not known\n"
    "fatal: Could not read from remote repository.\n"
)
CONNECTION_REFUSED_STDERR = (
    "fatal: unable to connect to 10.0.0.1:\n"
    "10.0.0.1: Connection refused\n"
)
CONNECTION_TIMEOUT_STDERR = (
    "ssh: connect to host example.com port 22: Operation timed out\n"
    "fatal: Could not read from remote repository.\n"
)
MALFORMED_URL_STDERR = (
    "fatal: protocol 'htp' is not supported\n"
)
UNKNOWN_STDERR = (
    "fatal: unexpected git failure: some exotic backend error\n"
)


@pytest.mark.parametrize(
    "stderr",
    [SSH_DENIED_STDERR, HTTPS_AUTH_STDERR, HTTPS_NO_PROMPT_STDERR],
)
def test_classify_lsremote_error_auth_failed(stderr):
    kind, detail = sm._classify_lsremote_error(stderr, 128)
    assert kind == "auth_failed"
    assert "autenticazione" in detail


def test_classify_lsremote_error_unreachable_host():
    kind, _ = sm._classify_lsremote_error(UNRESOLVABLE_HOST_STDERR, 128)
    assert kind == "unreachable"


@pytest.mark.parametrize(
    "stderr",
    [CONNECTION_REFUSED_STDERR, CONNECTION_TIMEOUT_STDERR],
)
def test_classify_lsremote_error_unreachable_network(stderr):
    kind, _ = sm._classify_lsremote_error(stderr, 128)
    assert kind == "unreachable"


def test_classify_lsremote_error_malformed_url():
    kind, _ = sm._classify_lsremote_error(MALFORMED_URL_STDERR, 128)
    assert kind == "malformed_url"


def test_classify_lsremote_error_unknown_keeps_raw_stderr():
    kind, detail = sm._classify_lsremote_error(UNKNOWN_STDERR, 42)
    assert kind == "unknown"
    assert "exotic backend error" in detail  # stderr grezzo allegato
    assert "42" in detail  # exit code allegato


def test_classify_lsremote_error_empty_stderr_is_unknown():
    kind, detail = sm._classify_lsremote_error("", 128)
    assert kind == "unknown"
    assert "nessun output" in detail


def test_classify_lsremote_error_case_insensitive():
    kind, _ = sm._classify_lsremote_error("FATAL: PERMISSION DENIED (PUBLICKEY).", 128)
    assert kind == "auth_failed"
# --- helper per remote popolato con branch di default arbitrario ---


def _bare_remote_with_branch(tmp_path: Path, branch_name: str = "master") -> Path:
    """Crea un remote bare con un branch di default e un commit iniziale."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

    work = tmp_path / "remote-work"
    work.mkdir()
    _git(work, "init")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    _git(work, "symbolic-ref", "HEAD", f"refs/heads/{branch_name}")
    (work / "sessions").mkdir()
    (work / "sessions" / "remote-session.yaml").write_text("id: remote-session\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "initial remote commit")
    _git(work, "push", str(remote), branch_name)
    return remote


# --- setup_git_sync: ramo (b) clone ---


def test_setup_clone_branch_populated_remote(tmp_home, tmp_path):
    """2.6: clone di un remote popolato su home vuota, branch di default diverso da main."""
    remote = _bare_remote_with_branch(tmp_path, "master")
    tmp_home.mkdir(parents=True)

    result = sm.setup_git_sync(f"file://{remote}", home=tmp_home)

    assert result["status"] == "ok"
    assert result["branch"] == "clone"
    assert (tmp_home / ".git").is_dir()
    assert (tmp_home / "sessions" / "remote-session.yaml").exists()
    # Il checkout deve aver usato il branch di default del remote (master).
    assert sm._remote_default_branch(tmp_home) == "master"
    assert sm.is_git_enabled(tmp_home) is True


# --- setup_git_sync: ramo (c) integrazione ---


def test_setup_integration_preserves_local_user_data(tmp_home, tmp_path):
    """2.7: home con dati utente + remote popolato → integrazione, nessun reset distruttivo."""
    remote = _bare_remote_with_branch(tmp_path, "main")
    tmp_home.mkdir(parents=True)
    (tmp_home / "sessions").mkdir()
    (tmp_home / "sessions" / "local-session.yaml").write_text("id: local-session\n", encoding="utf-8")

    result = sm.setup_git_sync(f"file://{remote}", home=tmp_home, confirm_merge=True)

    assert result["status"] == "ok"
    assert result["branch"] == "integrazione"
    assert (tmp_home / "sessions" / "local-session.yaml").exists()
    assert (tmp_home / "sessions" / "remote-session.yaml").exists()


def test_setup_integration_unrelated_histories(tmp_home, tmp_path):
    """2.8: home git con history locale + remote popolato con history non correlata."""
    remote = _bare_remote_with_branch(tmp_path, "main")
    tmp_home.mkdir(parents=True)
    (tmp_home / "sessions").mkdir()
    _git(tmp_home, "init")
    _git(tmp_home, "config", "user.email", "test@example.com")
    _git(tmp_home, "config", "user.name", "Test")
    (tmp_home / "sessions" / "local-session.yaml").write_text("id: local-session\n", encoding="utf-8")
    _git(tmp_home, "add", "-A")
    _git(tmp_home, "commit", "-m", "local initial")

    result = sm.setup_git_sync(f"file://{remote}", home=tmp_home, confirm_merge=True)

    assert result["status"] == "ok"
    assert result["branch"] == "integrazione"
    assert (tmp_home / "sessions" / "local-session.yaml").exists()
    assert (tmp_home / "sessions" / "remote-session.yaml").exists()
    assert (tmp_home / "registry.md").exists()  # vista rigenerata


def test_setup_integration_requires_confirm(tmp_home, tmp_path):
    """2.10: ramo integrazione senza conferma → needs_confirm senza side-effect."""
    remote = _bare_remote_with_branch(tmp_path, "main")
    tmp_home.mkdir(parents=True)
    _git(tmp_home, "init")

    result = sm.setup_git_sync(f"file://{remote}", home=tmp_home)

    assert result["status"] == "needs_confirm"
    assert result["reason"] == "merge_with_local_data"
    assert result["branch"] == "integrazione"
    # nessun remote configurato, nessun side-effect
    assert _git(tmp_home, "remote").stdout.strip() == ""


# --- setup_git_sync: guard e fallback ---


def test_setup_already_configured_noop(tmp_home, bare_remote):
    """2.9: setup su home già configurata → no-op."""
    sm.init_git_sync(f"file://{bare_remote}", tmp_home)

    result = sm.setup_git_sync(f"file://{bare_remote}", home=tmp_home)

    assert result["status"] == "ok"
    assert result["branch"] is None
    assert "già configurato" in result["message"]


def test_setup_init_toctou_fallback_to_integration(monkeypatch, tmp_home, tmp_path):
    """2.11: validazione dice vuoto ma il push fallisce perché il remote è popolato → integrazione."""
    remote = _bare_remote_with_branch(tmp_path, "main")

    calls = []
    original_validate_remote = sm.validate_remote

    def fake_validate(url):
        calls.append(1)
        if len(calls) == 1:
            return {"ok": True, "state": "empty"}
        return {"ok": True, "state": "populated"}

    monkeypatch.setattr(sm, "validate_remote", fake_validate)

    result = sm.setup_git_sync(f"file://{remote}", home=tmp_home, confirm_merge=True)

    assert result["status"] == "ok"
    assert result["branch"] == "integrazione"
    assert (tmp_home / "sessions" / "remote-session.yaml").exists()
