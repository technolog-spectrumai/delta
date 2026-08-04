"""Cancellable background jobs, for work too slow to run on the UI thread.

Two shapes, because the two kinds of work cancel differently:

:class:`Job` runs in-process Python over a countable number of files — packaging.
Progress is honest because we count the units ourselves, and cancellation is a
``threading.Event`` the work function checks between them. Modelled on enigma's
map_packager PackageWorker; shelling out to ``zip`` would cost the per-file
progress that makes the bar meaningful.

:class:`ProcessJob` runs a child process — a deploy. Neither of those properties
survives: a ``docker compose up --build`` has no countable progress, and an
event this process checks cannot stop work happening in a process tree it does
not own. So it takes the other half of enigma's design, the part Job does not
need: ``subprocess.Popen(start_new_session=True)`` plus a SIGTERM→SIGKILL
escalation over the child's **process group**.

One load-bearing behaviour per class, both easy to get wrong:

* ``Job`` removes the partial output on **both** the failure and the cancel
  path. A truncated archive left on disk looks like a successful one.
* ``ProcessJob`` escalates to SIGKILL from a **timer**, not after its reader
  loop. The loop blocks until every writer closes the pipe, so a child that
  ignores SIGTERM would hold it open past the grace period and the SIGKILL
  would never be sent.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal


class Cancelled(Exception):
    """Raised out of a work function when the cancel event is set."""


class Job(QThread):
    """Run one function off the UI thread, with progress, logging and cancel.

    The work function is called as ``work(report)`` where ``report`` is a
    :class:`Reporter` it should use for status, log lines and progress, and
    whose :meth:`Reporter.check_cancelled` it should call at each unit of work.
    It returns the path it produced (or None).
    """

    # Named job_* to avoid colliding with QThread's own `finished`, which the
    # UI also connects.
    status_changed = pyqtSignal(str)
    log_message = pyqtSignal(str)
    progress_changed = pyqtSignal(int, int)   # done, total
    job_finished = pyqtSignal(str)            # the artifact path
    job_failed = pyqtSignal(str)
    job_cancelled = pyqtSignal()

    def __init__(self, work: Callable[["Reporter"], Path | None],
                 *, cleanup: Path | None = None) -> None:
        super().__init__()
        self._work = work
        self._cleanup = cleanup
        self.cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def _discard_partial(self) -> None:
        if self._cleanup is not None:
            Path(self._cleanup).unlink(missing_ok=True)

    def run(self) -> None:  # noqa: D102 - QThread entry point
        reporter = Reporter(self)
        try:
            produced = self._work(reporter)
        except Cancelled:
            self._discard_partial()
            self.job_cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            self._discard_partial()
            self.job_failed.emit(str(exc))
        else:
            self.job_finished.emit(str(produced) if produced else "")


class Reporter:
    """The work function's channel back to the UI.

    Progress is throttled to roughly 1% granularity: a payload of several
    thousand files would otherwise flood the signal queue with repaints and
    make the job slower than the work itself.
    """

    def __init__(self, job: Job) -> None:
        self._job = job
        self._last_bucket = -1

    def check_cancelled(self) -> None:
        if self._job.cancel_event.is_set():
            raise Cancelled()

    def status(self, text: str) -> None:
        self.check_cancelled()
        self._job.status_changed.emit(text)

    def log(self, text: str) -> None:
        self._job.log_message.emit(text)

    def progress(self, done: int, total: int) -> None:
        self.check_cancelled()
        if total <= 0:
            return
        bucket = done * 100 // total
        if done >= total or bucket != self._last_bucket:
            self._last_bucket = bucket
            self._job.progress_changed.emit(done, total)


class ProcessJob(QThread):
    """Run one child process, streaming its merged output to the UI.

    The child gets its own session so cancellation can reach the whole tree —
    ``docker compose`` spawns work this process never sees. Output is batched
    before it crosses the thread boundary: a cold build emits thousands of lines
    in seconds, and one queued signal per line stalls the UI more than the build
    does.
    """

    log_message = pyqtSignal(str)     # one or more already-cleaned lines
    job_finished = pyqtSignal(int)    # exit code
    job_failed = pyqtSignal(str)      # could not even start
    job_cancelled = pyqtSignal()

    GRACE_SECONDS = 10.0
    FLUSH_SECONDS = 0.1
    FLUSH_LINES = 200

    def __init__(self, argv: list[str], *, cwd: Path,
                 env: dict[str, str] | None = None,
                 clean: Callable[[str], str] | None = None) -> None:
        super().__init__()
        self._argv = list(argv)
        self._cwd = Path(cwd)
        self._env = env
        self._clean = clean or (lambda line: line.rstrip("\n"))
        self._cancel = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._killer: threading.Timer | None = None
        self._lock = threading.Lock()

    # -- cancellation -----------------------------------------------------
    def request_cancel(self) -> None:
        if self._cancel.is_set():
            return
        self._cancel.set()
        self._signal_group(signal.SIGTERM)
        killer = threading.Timer(self.GRACE_SECONDS,
                                 lambda: self._signal_group(signal.SIGKILL))
        killer.daemon = True
        self._killer = killer
        killer.start()

    def _signal_group(self, sig: int) -> None:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    # -- the thread -------------------------------------------------------
    def run(self) -> None:  # noqa: D102 - QThread entry point
        try:
            proc = subprocess.Popen(
                self._argv,
                cwd=str(self._cwd),
                env=self._env,
                stdin=subprocess.DEVNULL,   # a prompt must fail, never hang
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",           # a stray byte must not kill the reader
                bufsize=1,
                start_new_session=True,     # its own process group, for the kill
            )
        except (OSError, ValueError) as exc:
            self.job_failed.emit(str(exc))
            return

        with self._lock:
            self._proc = proc
        if self._cancel.is_set():
            # Cancelled between construction and start.
            self._signal_group(signal.SIGTERM)

        try:
            self._pump(proc)
            code = proc.wait()
        except Exception as exc:  # noqa: BLE001 - surfaced, never swallowed
            self.job_failed.emit(str(exc))
            return
        finally:
            if self._killer is not None:
                self._killer.cancel()
            if proc.stdout is not None:
                proc.stdout.close()

        if self._cancel.is_set():
            self.job_cancelled.emit()
        else:
            self.job_finished.emit(code)

    def _pump(self, proc: subprocess.Popen) -> None:
        batch: list[str] = []
        last = time.monotonic()
        stream = proc.stdout
        if stream is None:
            return
        for raw in stream:
            batch.append(self._clean(raw))
            now = time.monotonic()
            if len(batch) >= self.FLUSH_LINES or (now - last) >= self.FLUSH_SECONDS:
                self.log_message.emit("\n".join(batch))
                batch.clear()
                last = now
        if batch:
            self.log_message.emit("\n".join(batch))
