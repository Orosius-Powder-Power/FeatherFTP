from __future__ import annotations
import calendar
import re
from datetime import datetime

from .errors import FtpProtocolError
from .models import FtpEntry, FtpEntryType

_PASV_RE = re.compile(r"\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)")
_EPSV_RE = re.compile(r"\(\|\|\|(\d+)\|\)")
_UNIX_LIST_RE = re.compile(
    r"^(?P<perm>[bcdlps-][rwxstST-]{9})\s+"
    r"\d+\s+\S+\s+\S+\s+"
    r"(?P<size>\d+)\s+"
    r"(?P<month>[A-Za-z]{3})\s+"
    r"(?P<day>\d{1,2})\s+"
    r"(?P<timeyear>\d{2}:\d{2}|\d{4})\s+"
    r"(?P<name>.+)$"
)
_WINDOWS_LIST_RE = re.compile(
    r"^(?P<date>\d{2}-\d{2}-\d{2})\s+"
    r"(?P<time>\d{2}:\d{2})(?P<ampm>[AP]M)\s+"
    r"(?P<size_dir><DIR>|\d+)\s+"
    r"(?P<name>.+)$",
    re.IGNORECASE,
)


def parse_pasv_response(message: str) -> tuple[str, int]:
    match = _PASV_RE.search(message)
    if not match:
        raise FtpProtocolError(f"Invalid PASV response: {message}")
    parts = [int(part) for part in match.groups()]
    host = ".".join(str(part) for part in parts[:4])
    port = parts[4] * 256 + parts[5]
    return host, port


def parse_epsv_response(message: str, fallback_host: str) -> tuple[str, int]:
    match = _EPSV_RE.search(message)
    if not match:
        raise FtpProtocolError(f"Invalid EPSV response: {message}")
    return fallback_host, int(match.group(1))


def parse_mlsx_line(line: str) -> FtpEntry:
    facts_text, _, name = line.partition(" ")
    facts: dict[str, str] = {}
    for item in facts_text.split(";"):
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        facts[key.lower()] = value

    entry_type = {
        "dir": FtpEntryType.DIRECTORY,
        "file": FtpEntryType.FILE,
        "cdir": FtpEntryType.DIRECTORY,
        "pdir": FtpEntryType.DIRECTORY,
    }.get(facts.get("type", "").lower(), FtpEntryType.UNKNOWN)
    size = _safe_int(facts.get("size"))
    modified = _parse_mlsx_time(facts.get("modify"))
    return FtpEntry(
        name=name.strip(),
        type=entry_type,
        size=size,
        modified=modified,
        permissions=facts.get("perm", ""),
        raw=line,
        facts=facts,
    )


def parse_list_line(line: str, now: datetime | None = None) -> FtpEntry:
    now = now or datetime.now()
    unix = _UNIX_LIST_RE.match(line)
    if unix:
        perm = unix.group("perm")
        name = unix.group("name")
        if " -> " in name and perm.startswith("l"):
            name = name.split(" -> ", 1)[0]
        return FtpEntry(
            name=name,
            type=_type_from_unix_perm(perm),
            size=int(unix.group("size")),
            modified=_parse_unix_time(
                unix.group("month"),
                int(unix.group("day")),
                unix.group("timeyear"),
                now,
            ),
            permissions=perm,
            raw=line,
        )

    windows = _WINDOWS_LIST_RE.match(line)
    if windows:
        size_dir = windows.group("size_dir")
        is_dir = size_dir.upper() == "<DIR>"
        return FtpEntry(
            name=windows.group("name").strip(),
            type=FtpEntryType.DIRECTORY if is_dir else FtpEntryType.FILE,
            size=None if is_dir else int(size_dir),
            modified=_parse_windows_time(
                windows.group("date"),
                windows.group("time"),
                windows.group("ampm"),
            ),
            raw=line,
        )

    return FtpEntry(name=line.strip(), type=FtpEntryType.UNKNOWN, raw=line)


def parse_directory_listing(text: str, prefer_mlsx: bool = False) -> list[FtpEntry]:
    entries: list[FtpEntry] = []
    for raw_line in text.splitlines():
        line = raw_line.strip("\r\n")
        if not line:
            continue
        entry = parse_mlsx_line(
            line) if prefer_mlsx and " " in line else parse_list_line(line)
        if entry.name not in {".", ".."}:
            entries.append(entry)
    return entries


def _type_from_unix_perm(perm: str) -> FtpEntryType:
    marker = perm[0]
    if marker == "d":
        return FtpEntryType.DIRECTORY
    if marker == "l":
        return FtpEntryType.LINK
    if marker == "-":
        return FtpEntryType.FILE
    return FtpEntryType.UNKNOWN


def _parse_unix_time(month: str, day: int, timeyear: str, now: datetime) -> datetime | None:
    try:
        month_number = list(calendar.month_abbr).index(month.title())
        if ":" in timeyear:
            hour, minute = [int(part) for part in timeyear.split(":", 1)]
            candidate = datetime(now.year, month_number, day, hour, minute)
            if candidate > now:
                candidate = candidate.replace(year=now.year - 1)
            return candidate
        return datetime(int(timeyear), month_number, day)
    except (ValueError, IndexError):
        return None


def _parse_windows_time(date_text: str, time_text: str, ampm: str) -> datetime | None:
    try:
        month, day, year = [int(part) for part in date_text.split("-")]
        hour, minute = [int(part) for part in time_text.split(":")]
        if ampm.upper() == "PM" and hour != 12:
            hour += 12
        if ampm.upper() == "AM" and hour == 12:
            hour = 0
        year += 2000 if year < 70 else 1900
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def _parse_mlsx_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
