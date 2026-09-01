import unittest
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.core.auth import current_active_user
from app.models.user import User


class ChatApiTests(unittest.TestCase):
    def _override_current_user(self):
        return User(
            id=uuid.uuid4(),
            username="chatuser",
            email="chatuser@example.com",
            hashed_password="hashed",
            level=3,
            department="Operaciones",
            nombre_completo="Usuario Chat",
            cargo="Analista",
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )

    def test_chat_api_supports_messages_with_report_metadata(self):
        app.dependency_overrides[current_active_user] = self._override_current_user
        client = TestClient(app)

        try:
            response = client.post(
                "/api/chat/",
                json={
                    "content": "Necesito la información del reporte de seguridad del turno.",
                    "message_type": "question",
                    "reference_title": "Reporte de seguridad",
                    "reference_url": "/dashboard/seguridad-permisos",
                },
            )

            self.assertEqual(response.status_code, 201, response.text)
            body = response.json()
            self.assertEqual(body["message_type"], "question")
            self.assertEqual(body["reference_title"], "Reporte de seguridad")
            self.assertEqual(body["reference_url"], "/dashboard/seguridad-permisos")

            list_response = client.get("/api/chat/")
            self.assertEqual(list_response.status_code, 200, list_response.text)
            items = list_response.json()
            self.assertTrue(any(item["content"] == body["content"] for item in items))
        finally:
            app.dependency_overrides.clear()


if __name__ == '__main__':
    unittest.main()
