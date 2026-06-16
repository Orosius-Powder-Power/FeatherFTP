from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


APP_DIR_NAME = "SocketFTPClient"


@dataclass(slots=True)
class Site:
    id: int | None
    name: str
    host: str
    port: int = 21
    username: str = "anonymous"
    save_password: bool = False
    password: str = ""


class SiteStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def list_sites(self) -> list[Site]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, host, port, username, save_password, password "
                "FROM sites ORDER BY updated_at DESC, name"
            ).fetchall()
        return [
            Site(
                id=row["id"],
                name=row["name"],
                host=row["host"],
                port=row["port"],
                username=row["username"],
                save_password=bool(row["save_password"]),
                password=row["password"] or "",
            )
            for row in rows
        ]

    def save_site(self, site: Site) -> int:
        password = site.password if site.save_password else ""
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as conn:
            if site.id is None:
                cursor = conn.execute(
                    "INSERT INTO sites(name, host, port, username, save_password, password, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        site.name,
                        site.host,
                        site.port,
                        site.username,
                        int(site.save_password),
                        password,
                        now,
                    ),
                )
                return int(cursor.lastrowid)
            conn.execute(
                "UPDATE sites SET name=?, host=?, port=?, username=?, save_password=?, "
                "password=?, updated_at=? WHERE id=?",
                (
                    site.name,
                    site.host,
                    site.port,
                    site.username,
                    int(site.save_password),
                    password,
                    now,
                    site.id,
                ),
            )
            return site.id

    def delete_site(self, site_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sites WHERE id=?", (site_id,))

    def record_transfer(self, kind: str, remote_path: str, local_path: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO transfer_history(kind, remote_path, local_path, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind, remote_path, local_path, status, datetime.now(UTC).isoformat(timespec="seconds")),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL DEFAULT 21,
                    username TEXT NOT NULL DEFAULT 'anonymous',
                    save_password INTEGER NOT NULL DEFAULT 0,
                    password TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transfer_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    remote_path TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )


def default_db_path() -> Path:
    import os

    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_DIR_NAME / "client.sqlite3"
    return Path.home() / ".socket_ftp_client" / "client.sqlite3"
