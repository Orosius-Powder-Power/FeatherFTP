from __future__ import annotations

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
