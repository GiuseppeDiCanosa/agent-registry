"""Test di concorrenza cross-process per lock_manager.py."""

import multiprocessing
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import lock_manager as lm


def _worker(
    path: str, lock_dir: str, hold_seconds: float, acquired_event, result_queue
) -> None:
    """Processo worker che acquisisce un lock, attende e rilascia."""
    lm.LOCK_DIR = Path(lock_dir)
    lm._OPEN_LOCK_FDS.clear()
    result = lm.acquire_lock(path, "worker")
    result_queue.put(result)
    acquired_event.set()
    time.sleep(hold_seconds)
    lm.release_lock(path, "worker")
    lm._OPEN_LOCK_FDS.clear()


def test_cross_process_lock(tmp_path):
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    target_path = "/tmp/cross-process-test.txt"

    lm.LOCK_DIR = lock_dir
    lm._OPEN_LOCK_FDS.clear()

    acquired_event = multiprocessing.Event()
    result_queue = multiprocessing.Queue()

    process = multiprocessing.Process(
        target=_worker,
        args=(target_path, str(lock_dir), 0.5, acquired_event, result_queue),
    )
    process.start()
    acquired_event.wait(timeout=2.0)

    worker_result = result_queue.get(timeout=1.0)
    assert worker_result["locked"] is True, f"worker failed to lock: {worker_result}"

    # Questo processo non deve riuscire ad acquisire il lock
    result = lm.acquire_lock(target_path, "main")
    assert result["locked"] is False
    assert result.get("session_id") == "worker"

    process.join(timeout=2.0)
    if process.is_alive():
        process.terminate()
        process.join()

    # Dopo che il worker ha rilasciato, questo processo può acquisire
    lm._OPEN_LOCK_FDS.clear()
    result2 = lm.acquire_lock(target_path, "main")
    assert result2["locked"] is True
    assert result2["owner"] == "main"

    lm.release_lock(target_path, "main")
