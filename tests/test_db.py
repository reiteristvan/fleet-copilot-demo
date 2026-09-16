"""Database URL handling, which two other components depend on agreeing about."""

from __future__ import annotations

import pytest

from fleet_copilot.db import PSYCOPG_SCHEME, sqlalchemy_url, with_credentials


class TestSqlalchemyUrl:
    def test_rewrites_the_bare_scheme_to_psycopg_3(self) -> None:
        """A bare postgresql:// resolves to psycopg2, which is not installed."""
        url = sqlalchemy_url("postgresql://fleet:fleet@localhost:5432/fleet_copilot")
        assert url == f"{PSYCOPG_SCHEME}://fleet:fleet@localhost:5432/fleet_copilot"

    def test_leaves_an_explicit_driver_alone(self) -> None:
        url = f"{PSYCOPG_SCHEME}://fleet:fleet@localhost:5432/fleet_copilot"
        assert sqlalchemy_url(url) == url

    def test_rewrites_only_the_scheme(self) -> None:
        """A database or password containing the scheme text must survive."""
        url = "postgresql://u:postgresql://@localhost:5432/db"
        assert sqlalchemy_url(url).count(f"{PSYCOPG_SCHEME}://") == 1


class TestWithCredentials:
    def test_swaps_the_role_and_keeps_everything_else(self) -> None:
        url = with_credentials(
            "postgresql://fleet:fleet@localhost:5432/fleet_copilot",
            user="copilot_ro",
            password="copilot_ro",
        )
        assert url == "postgresql://copilot_ro:copilot_ro@localhost:5432/fleet_copilot"

    def test_keeps_the_database_when_there_is_no_port(self) -> None:
        url = with_credentials(
            "postgresql://fleet:fleet@db/fleet_copilot", user="ro", password="ro"
        )
        assert url == "postgresql://ro:ro@db/fleet_copilot"

    def test_escapes_a_password_that_would_break_the_url(self) -> None:
        """A password with an @ or a / silently reshapes the authority."""
        url = with_credentials(
            "postgresql://fleet:fleet@localhost:5432/fleet_copilot",
            user="ro",
            password="p@ss/word",
        )
        assert "p%40ss%2Fword" in url
        assert url.endswith("/fleet_copilot")

    def test_refuses_a_url_with_no_host(self) -> None:
        with pytest.raises(ValueError, match="no host"):
            with_credentials("postgresql:///fleet_copilot", user="ro", password="ro")
