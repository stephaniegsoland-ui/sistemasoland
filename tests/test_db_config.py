import os
import unittest
from unittest.mock import patch

from app.core.db import normalize_database_url, resolve_database_url


class DatabaseUrlNormalizationTests(unittest.TestCase):
    def test_localhost_is_rewritten_for_async_mysql(self):
        original = "mysql+pymysql://soland:soland_password@localhost/soland_db"
        self.assertEqual(
            normalize_database_url(original),
            "mysql+aiomysql://soland:soland_password@127.0.0.1/soland_db",
        )

    def test_non_localhost_is_left_unchanged(self):
        original = "mysql+aiomysql://soland:soland_password@db.internal/soland_db"
        self.assertEqual(normalize_database_url(original), original)

    def test_mysql_urls_fallback_to_sqlite_when_enabled(self):
        with patch.dict(os.environ, {"USE_SQLITE_FALLBACK": "true"}, clear=False):
            url = resolve_database_url("mysql+pymysql://soland:soland_password@localhost/soland_db")
            self.assertTrue(url.startswith("sqlite+aiosqlite:///"))
            self.assertTrue(url.endswith("soland.db"))


if __name__ == "__main__":
    unittest.main()
