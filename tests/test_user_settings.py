import unittest

from pydantic import ValidationError

from app.api.endpoints.users import UserPasswordChange, UserProfileUpdate, _authorize_user_update_fields
from app.models.user import User


class UserSettingsSchemaTests(unittest.TestCase):
    def test_profile_update_accepts_only_personal_fields(self):
        profile = UserProfileUpdate(username="operator", email="operator@example.com", department="Operaciones")
        self.assertEqual(profile.username, "operator")
        self.assertEqual(profile.department, "Operaciones")

        with self.assertRaises(ValidationError):
            UserProfileUpdate.model_validate({"level": 1, "is_superuser": True})

    def test_password_change_requires_a_new_password_of_at_least_eight_characters(self):
        with self.assertRaises(ValidationError):
            UserPasswordChange(current_password="old", new_password="short")

        payload = UserPasswordChange(current_password="old", new_password="new-pass-123")
        self.assertEqual(payload.new_password, "new-pass-123")

    def test_self_cannot_change_privileges_and_supervisor_cannot_assign_permissions(self):
        self_user = User(username="self", email="self@example.com", hashed_password="hash", level=3)
        with self.assertRaises(Exception) as self_error:
            _authorize_user_update_fields(self_user.id, self_user, {"level"})
        self.assertEqual(getattr(self_error.exception, "status_code", None), 403)

        supervisor = User(username="supervisor", email="supervisor@example.com", hashed_password="hash", level=2)
        target_id = User(username="target", email="target@example.com", hashed_password="hash", level=3).id
        with self.assertRaises(Exception) as supervisor_error:
            _authorize_user_update_fields(target_id, supervisor, {"permissions"})
        self.assertEqual(getattr(supervisor_error.exception, "status_code", None), 403)


if __name__ == "__main__":
    unittest.main()