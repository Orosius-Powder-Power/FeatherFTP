from __future__ import annotations

import itertools
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .models import FtpConnectionConfig, TransferKind, TransferRequest, TransferStatus
from .protocol import FtpSession, TransferInterrupted


TaskCallback = Callable[["TransferTask"], None]
SessionFactory = Callable[[], FtpSession]


@dataclass(slots=True)
class TransferTask:
    id: int
    request: TransferRequest
    status: TransferStatus = TransferStatus.QUEUED
    transferred: int = 0
    total: int | None = None
    speed: float = 0.0
    error: str = ""
    pause_event: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def title(self) -> str:
        if self.request.kind == TransferKind.DOWNLOAD:
            return f"Download {self.request.remote_path}"
        return f"Upload {self.request.local_path.name}"

    @property
    def percent(self) -> int:
        if not self.total:
            return 0
        return max(0, min(100, int(self.transferred * 100 / self.total)))


class TransferManager:
    def __init__(
        self,
        config_provider: Callable[[], FtpConnectionConfig | None],
        log_callback=None,
        task_callback: TaskCallback | None = None,
    ) -> None:
        self.config_provider = config_provider
        self.log_callback = log_callback
        self.task_callback = task_callback
        self._ids = itertools.count(1)
        self._queue: queue.Queue[TransferTask] = queue.Queue()
        self._tasks: dict[int, TransferTask] = {}
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, name="ftp-transfer-worker", daemon=True)
        self._worker.start()

    def enqueue_download(self, remote_path: str, local_path: str | Path, resume: bool = True) -> TransferTask:
        task = self._new_task(
            TransferRequest(
                kind=TransferKind.DOWNLOAD,
                remote_path=remote_path,
                local_path=Path(local_path),
                resume=resume,
            )
        )
        self._queue.put(task)
        self._notify(task)
        return task

    def enqueue_upload(self, local_path: str | Path, remote_path: str, resume: bool = True) -> TransferTask:
        task = self._new_task(
            TransferRequest(
                kind=TransferKind.UPLOAD,
                remote_path=remote_path,
                local_path=Path(local_path),
                resume=resume,
            )
        )
        self._queue.put(task)
        self._notify(task)
        return task

    def pause_task(self, task_id: int) -> None:
        task = self._tasks.get(task_id)
        if task and task.status == TransferStatus.RUNNING:
            task.pause_event.set()

    def resume_task(self, task_id: int) -> None:
        task = self._tasks.get(task_id)
        if task and task.status in {TransferStatus.PAUSED, TransferStatus.FAILED}:
            task.pause_event.clear()
            task.cancel_event.clear()
            task.error = ""
            task.status = TransferStatus.QUEUED
            self._queue.put(task)
            self._notify(task)

    def cancel_task(self, task_id: int) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task.cancel_event.set()
        if task.status == TransferStatus.QUEUED:
            task.status = TransferStatus.CANCELLED
            self._notify(task)

    def tasks(self) -> list[TransferTask]:
        with self._lock:
            return list(self._tasks.values())

    def _new_task(self, request: TransferRequest) -> TransferTask:
        task = TransferTask(id=next(self._ids), request=request)
        with self._lock:
            self._tasks[task.id] = task
        return task

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task.status == TransferStatus.CANCELLED:
                self._queue.task_done()
                continue
            self._execute(task)
            self._queue.task_done()

    def _execute(self, task: TransferTask) -> None:
        config = self.config_provider()
        if not config:
            task.status = TransferStatus.FAILED
            task.error = "No active FTP connection configuration"
            self._notify(task)
            return

        task.status = TransferStatus.RUNNING
        self._notify(task)
        session = FtpSession(log_callback=self.log_callback)
        try:
            session.connect(
                config.host,
                config.port,
                config.username,
                config.password,
                config.timeout,
                config.passive_mode,
            )
            if task.request.kind == TransferKind.DOWNLOAD:
                session.download_file(
                    task.request.remote_path,
                    task.request.local_path,
                    resume=task.request.resume,
                    progress=lambda done, total, speed: self._progress(task, done, total, speed),
                    pause_event=task.pause_event,
                    cancel_event=task.cancel_event,
                )
            else:
                session.upload_file(
                    task.request.local_path,
                    task.request.remote_path,
                    resume=task.request.resume,
                    progress=lambda done, total, speed: self._progress(task, done, total, speed),
                    pause_event=task.pause_event,
                    cancel_event=task.cancel_event,
                )
            task.status = TransferStatus.COMPLETED
            task.error = ""
        except TransferInterrupted as exc:
            if str(exc) == "cancelled":
                task.status = TransferStatus.CANCELLED
            else:
                task.status = TransferStatus.PAUSED
        except Exception as exc:
            task.status = TransferStatus.FAILED
            task.error = str(exc)
        finally:
            session.close()
            self._notify(task)

    def _progress(self, task: TransferTask, transferred: int, total: int | None, speed: float) -> None:
        task.transferred = transferred
        task.total = total
        task.speed = speed
        self._notify(task)

    def _notify(self, task: TransferTask) -> None:
        if self.task_callback:
            self.task_callback(task)


def compute_download_offset(local_path: Path, remote_size: int | None, resume: bool) -> int:
    if not resume or not local_path.exists():
        return 0
    offset = local_path.stat().st_size
    if remote_size is not None and offset > remote_size:
        return 0
    return offset


def compute_upload_offset(local_size: int, remote_size: int | None, resume: bool) -> int:
    if not resume or remote_size is None:
        return 0
    if remote_size > local_size:
        return 0
    return remote_size
