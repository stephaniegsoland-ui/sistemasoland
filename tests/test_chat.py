import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.endpoints.assistant import _build_local_answer
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

    def test_chat_message_creates_notification_for_recipient(self):
        sender = self._override_current_user()
        recipient = User(
            id=uuid.uuid4(),
            username="recipientuser",
            email="recipient@example.com",
            hashed_password="hashed",
            level=3,
            department="Operaciones",
            nombre_completo="Usuario Destinatario",
            cargo="Analista",
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        app.dependency_overrides[current_active_user] = lambda: sender
        client = TestClient(app)

        try:
            create_response = client.post(
                "/api/chat/",
                json={
                    "content": "Tienes un mensaje nuevo de prueba.",
                    "message_type": "info",
                    "recipient_id": str(recipient.id),
                },
            )
            self.assertEqual(create_response.status_code, 201, create_response.text)

            notifications_response = client.get("/api/notifications/")
            self.assertEqual(notifications_response.status_code, 200, notifications_response.text)
            items = notifications_response.json()
            self.assertTrue(any(item["message"].startswith("chatuser:") for item in items))
        finally:
            app.dependency_overrides.clear()

    def test_assistant_reports_live_business_data(self):
        snapshot = {
            "companies_count": 3,
            "vehicles_count": 12,
            "company_rifs": ["J-12345678-9", "V-98765432-1", "G-11111111-1"],
            "pending_retention_total": 4250.5,
            "pending_retention_count": 4,
        }

        retention_answer = _build_local_answer("¿Cuáles son mis retenciones pendientes y por cobrar?", snapshot)
        rif_answer = _build_local_answer("Dime el rif de mis empresas registradas", snapshot)
        vehicle_answer = _build_local_answer("Cuántos vehículos tengo registrados", snapshot)

        self.assertIn("4250.50", retention_answer)
        self.assertIn("J-12345678-9", rif_answer)
        self.assertIn("12", vehicle_answer)

    def test_assistant_combines_retentions_rifs_and_vehicles_in_one_answer(self):
        snapshot = {
            "companies_count": 3,
            "vehicles_count": 12,
            "company_rifs": ["J-12345678-9", "V-98765432-1", "G-11111111-1"],
            "pending_retention_total": 4250.5,
            "pending_retention_count": 4,
        }

        answer = _build_local_answer(
            "Necesito saber mis retenciones pendientes por cobrar, el rif de mis empresas registradas y cuántos vehículos tengo registrados",
            snapshot,
        )

        self.assertIn("4", answer)
        self.assertIn("4250.50", answer)
        self.assertIn("J-12345678-9", answer)
        self.assertIn("12", answer)

    def test_assistant_reports_no_pending_retentions_when_count_is_zero(self):
        snapshot = {
            "companies_count": 2,
            "vehicles_count": 7,
            "company_rifs": ["J-12345678-9", "V-98765432-1"],
            "pending_retention_total": 0,
            "pending_retention_count": 0,
        }

        answer = _build_local_answer("tengo retenciones pendientes", snapshot)

        self.assertIn("No tienes retenciones pendientes por cobrar", answer)
        self.assertNotIn("Tienes 0", answer)

    def test_assistant_endpoint_uses_backend_ai_when_available(self):
        app.dependency_overrides[current_active_user] = self._override_current_user
        client = TestClient(app)

        fake_response = {
            "choices": [{
                "message": {
                    "content": "El módulo de stock controla inventario y existencias."
                }
            }]
        }

        try:
            with patch("app.api.endpoints.assistant.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value.raise_for_status.return_value = None
                mock_post.return_value.json.return_value = fake_response

                response = client.post(
                    "/api/assistant/ask",
                    json={"question": "¿Qué hace el módulo de stock?"},
                )

                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertIn("stock", body["answer"].lower())
                self.assertEqual(body["provider"], "deepseek")
        finally:
            app.dependency_overrides.clear()


if __name__ == '__main__':
    unittest.main()
