from __future__ import annotations

import sqlite3

from ftp_client.store import Site, SiteStore


def test_site_store_does_not_save_password_by_default(tmp_path) -> None:
    store = SiteStore(tmp_path / "client.sqlite3")
    store.save_site(
        Site(
            id=None,
            name="Local",
            host="127.0.0.1",
            username="user",
            password="secret",
            save_password=False,
        )
    )

    sites = store.list_sites()
    assert len(sites) == 1
    assert sites[0].password == ""


def test_site_store_can_save_password_when_requested(tmp_path) -> None:
    store = SiteStore(tmp_path / "client.sqlite3")
    store.save_site(
        Site(
            id=None,
            name="Local",
            host="127.0.0.1",
            username="user",
            password="secret",
            save_password=True,
        )
    )

    assert store.list_sites()[0].password == "secret"


def test_site_store_updates_same_endpoint_instead_of_duplicating(tmp_path) -> None:
    store = SiteStore(tmp_path / "client.sqlite3")
    first_id = store.save_site(
        Site(
            id=None,
            name="First",
            host="127.0.0.1",
            port=2121,
            username="demo",
            password="old",
            save_password=True,
        )
    )
    second_id = store.save_site(
        Site(
            id=None,
            name="Renamed",
            host="127.0.0.1",
            port=2121,
            username="demo",
            password="new",
            save_password=True,
        )
    )

    sites = store.list_sites()
    assert second_id == first_id
    assert len(sites) == 1
    assert sites[0].name == "Renamed"
    assert sites[0].password == "new"


def test_site_store_dedupes_existing_database_rows(tmp_path) -> None:
    db_path = tmp_path / "client.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE sites (
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
        conn.executemany(
            "INSERT INTO sites(name, host, port, username, save_password, password, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("Old", "127.0.0.1", 2121, "demo", 0, "", "2026-01-01T00:00:00+00:00"),
                ("New", "127.0.0.1", 2121, "demo", 1, "secret", "2026-01-02T00:00:00+00:00"),
            ],
        )

    store = SiteStore(db_path)
    sites = store.list_sites()
    assert len(sites) == 1
    assert sites[0].name == "New"
    assert sites[0].password == "secret"
