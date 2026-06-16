from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable


class FtpEntryType(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    LINK = "link"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class FtpReply:
    code: int
    message: str
    lines: list[str] = field(default_factory=list)

    @property
    def is_positive(self) -> bool:
        return 100 <= self.code < 400

    @property
    def is_preliminary(self) -> bool:
        return 100 <= self.code < 200


@dataclass(slots=True)
class FtpEntry:
    name: str
    type: FtpEntryType
    size: int | None = None
    modified: datetime | None = None
    permissions: str = ""
    raw: str = ""
    facts: dict[str, str] = field(default_factory=dict)

    @property
    def is_dir(self) -> bool:
        return self.type == FtpEntryType.DIRECTORY


@dataclass(slots=True)
class FtpConnectionConfig:
    host: str
    port: int = 21
    username: str = "anonymous"
    password: str = "anonymous@"
    timeout: float = 15.0
    passive_mode: bool = True


class TransferKind(str, Enum):
    DOWNLOAD = "download"
    UPLOAD = "upload"


class TransferStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ProgressCallback = Callable[[int, int | None, float], None]
LogCallback = Callable[[str, str], None]


@dataclass(slots=True)
class TransferRequest:
    kind: TransferKind
    remote_path: str
    local_path: Path
    resume: bool = True
