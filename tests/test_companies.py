import unittest

from fastapi.testclient import TestClient

from app.main import app


class CompanyApiTests(unittest.TestCase):
    def test_companies_endpoint_is_available(self):
        client = TestClient(app)
        response = client.get('/api/admin/companies')
        self.assertIn(response.status_code, (200, 401))

    def test_admin_overview_endpoint_returns_summary(self):
        client = TestClient(app)
        response = client.get('/api/admin/overview')
        self.assertIn(response.status_code, (200, 401))
        if response.status_code == 200:
            payload = response.json()
            self.assertIn('total_retentions_month', payload)
            self.assertIn('pending_amount', payload)
            self.assertIn('companies_count', payload)
            self.assertIn('retentions_last_7', payload)


if __name__ == '__main__':
    unittest.main()
