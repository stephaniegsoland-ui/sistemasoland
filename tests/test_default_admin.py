import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.auth import DEFAULT_ADMIN_USERNAME, ensure_default_admin_user
from app.models.user import User


class DefaultAdminBootstrapTests(unittest.TestCase):
    def test_existing_admin_password_is_preserved_without_explicit_reset(self):
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
        session.add = MagicMock()

        mock_password_helper = MagicMock()

        with patch("app.core.auth.sessionmaker") as sessionmaker_mock, patch(
            "app.core.auth.PasswordHelper", return_value=mock_password_helper
        ), patch("app.core.auth.BOOTSTRAP_ADMIN", True), patch(
            "app.core.auth.DEFAULT_ADMIN_PASSWORD", "configured-password"
        ), patch("app.core.auth.RESET_DEFAULT_ADMIN_PASSWORD", False):
            sessionmaker_mock.return_value.return_value.__aenter__.return_value = session

            asyncio.run(ensure_default_admin_user())

        self.assertEqual(session.execute.await_count, 1)
        session.add.assert_not_called()
        session.commit.assert_not_awaited()
        self.assertEqual(existing_user.hashed_password, "old_hash")
        mock_password_helper.hash.assert_not_called()

    def test_existing_admin_password_can_be_reset_explicitly(self):
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
        session.add = MagicMock()
        mock_password_helper = MagicMock()
        mock_password_helper.hash.return_value = "new_hash"

        with patch("app.core.auth.sessionmaker") as sessionmaker_mock, patch(
            "app.core.auth.PasswordHelper", return_value=mock_password_helper
        ), patch("app.core.auth.BOOTSTRAP_ADMIN", True), patch(
            "app.core.auth.DEFAULT_ADMIN_PASSWORD", "configured-password"
        ), patch("app.core.auth.RESET_DEFAULT_ADMIN_PASSWORD", True):
            sessionmaker_mock.return_value.return_value.__aenter__.return_value = session

            asyncio.run(ensure_default_admin_user())

        session.add.assert_called_once_with(existing_user)
        session.commit.assert_awaited_once()
        self.assertEqual(existing_user.hashed_password, "new_hash")
        self.assertEqual(existing_user.level, 1)
        self.assertTrue(existing_user.is_active)
        self.assertTrue(existing_user.is_superuser)
        self.assertTrue(existing_user.is_verified)


if __name__ == "__main__":
    unittest.main()
