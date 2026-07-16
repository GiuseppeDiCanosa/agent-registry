"""Test per lock_manager.py."""

import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import lock_manager as lm


@pytest.fixture
def tmp_lock_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(lm, "LOCK_DIR", Path(td) / "locks")
        lm._OPEN_LOCK_FDS.clear()
        yield
        lm._OPEN_LOCK_FDS.clear()


def test_acquire_and_release(tmp_lock_dir):
    result = lm.acquire_lock("/tmp/test_acquire.txt", "sid-1")
    assert result["locked"] is True
    assert result["owner"] == "sid-1"

    status = lm.is_locked("/tmp/test_acquire.txt")
    assert status["locked"] is True
    assert status["session_id"] == "sid-1"

    release = lm.release_lock("/tmp/test_acquire.txt", "sid-1")
    assert release["released"] is True

    status = lm.is_locked("/tmp/test_acquire.txt")
    assert status["locked"] is False


def test_cannot_acquire_locked_file(tmp_lock_dir):
    lm.acquire_lock("/tmp/test_busy.txt", "sid-1")
    result = lm.acquire_lock("/tmp/test_busy.txt", "sid-2")
    assert result["locked"] is False
    assert result["session_id"] == "sid-1"


def test_check_warns_when_locked(tmp_lock_dir):
    lm.acquire_lock("/tmp/test_warn.txt", "sid-1")
    check = lm.check_and_warn("/tmp/test_warn.txt", "sid-2")
    assert check["ok"] is False
    assert "locked" in check["warning"]
    assert check["owner"] == "sid-1"


def test_heartbeat_renews_lock(tmp_lock_dir):
    lm.acquire_lock("/tmp/test_heartbeat.txt", "sid-1")
    before_ts = lm._read_info(lm._lock_file("/tmp/test_heartbeat.txt")).get("timestamp")
    time.sleep(0.1)
    heartbeat = lm.heartbeat("/tmp/test_heartbeat.txt", "sid-1")
    assert heartbeat["ok"] is True
    after_ts = lm._read_info(lm._lock_file("/tmp/test_heartbeat.txt")).get("timestamp")
    assert after_ts > before_ts
    after = lm.is_locked("/tmp/test_heartbeat.txt")
    assert after["age"] < 0.05


def test_heartbeat_fails_for_non_owner(tmp_lock_dir):
    lm.acquire_lock("/tmp/test_hb_owner.txt", "sid-1")
    heartbeat = lm.heartbeat("/tmp/test_hb_owner.txt", "sid-2")
    assert heartbeat["ok"] is False
    assert heartbeat["error"] == "not owner"


def test_stale_lock_is_released(tmp_lock_dir):
    lm.acquire_lock("/tmp/test_stale.txt", "sid-1", timeout=0.2)
    time.sleep(0.3)
    status = lm.is_locked("/tmp/test_stale.txt", timeout=0.2)
    assert status["locked"] is False
    assert status.get("stale_owner") == "sid-1"


def test_release_fails_for_non_owner(tmp_lock_dir):
    lm.acquire_lock("/tmp/test_release.txt", "sid-1")
    result = lm.release_lock("/tmp/test_release.txt", "sid-2")
    assert result["released"] is False
    assert result["error"] == "not owner"
