import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.auth import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, ensure_default_admin_user
from app.models.user import User


class DefaultAdminBootstrapTests(unittest.TestCase):
    def test_ensure_default_admin_user_rehashes_existing_admin_when_password_is_unknown(self):
        existing_user = User(
            username=DEFAULT_ADMIN_USERNAME,
            email="admin@soland.com",
            level=3,
            is_active=False,
            is_superuser=False,
            is_verified=False,
            hashed_password="old_hash",
        )

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing_user)))

        mock_password_helper = MagicMock()
        mock_password_helper.verify.return_value = False
        mock_password_helper.hash.return_value = "new_hash"

        with patch("app.core.auth.sessionmaker") as sessionmaker_mock, patch(
            "app.core.auth.PasswordHelper", return_value=mock_password_helper
        ):
            sessionmaker_mock.return_value.return_value.__aenter__.return_value = session

            asyncio.run(ensure_default_admin_user())

        self.assertEqual(session.execute.await_count, 1)
        self.assertEqual(session.add.call_count, 1)
        session.commit.assert_awaited_once()
        self.assertEqual(session.add.call_args.args[0].hashed_password, "new_hash")
        self.assertEqual(session.add.call_args.args[0].level, 1)


if __name__ == "__main__":
    unittest.main()
