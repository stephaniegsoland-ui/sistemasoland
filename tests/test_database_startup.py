import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("USE_SQLITE_FALLBACK", "false")

from sqlalchemy.exc import OperationalError

from app.core import db


def transient_database_error():
    database_error = OperationalError(
        "SELECT 1",
        {},
        Exception(2013, "Lost connection to MySQL server during query"),
    )
    wrapped_error = RuntimeError("Database initialization failed")
    wrapped_error.__cause__ = database_error
    return wrapped_error


class DatabaseStartupTests(unittest.TestCase):
    def test_retries_transient_mysql_connection_failure(self):
        with patch(
            "app.core.db._create_db_and_tables_once",
            new_callable=AsyncMock,
        ) as initialize, patch(
            "app.core.db.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            initialize.side_effect = [transient_database_error(), None]

            asyncio.run(db.create_db_and_tables())

        self.assertEqual(initialize.await_count, 2)
        sleep.assert_awaited_once_with(2)

    def test_does_not_retry_non_connection_failure(self):
        with patch(
            "app.core.db._create_db_and_tables_once",
            new_callable=AsyncMock,
        ) as initialize, patch(
            "app.core.db.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            initialize.side_effect = RuntimeError("Database schema is invalid")

            with self.assertRaisesRegex(RuntimeError, "Database schema is invalid"):
                asyncio.run(db.create_db_and_tables())

        initialize.assert_awaited_once()
        sleep.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
