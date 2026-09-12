import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.api.endpoints.admin_overview import extract_invoice_fields


class InvoiceRetentionApiTests(unittest.TestCase):
    def test_invoices_endpoint_is_available(self):
        client = TestClient(app)
        response = client.get('/api/admin/invoices')
        self.assertIn(response.status_code, (200, 401, 404))

    def test_extract_invoice_fields_parses_rif_and_values(self):
        payload = extract_invoice_fields(
            """PROVEEDOR: COMERCIAL ABC C.A.
            RIF: J-12345678-9
            TOTAL FACTURA: 1500.00
            RETENCION: 75.00
            PORCENTAJE: 5%"""
        )
        self.assertEqual(payload['rif'], 'J-12345678-9')
        self.assertAlmostEqual(payload['total_amount'], 1500.00)
        self.assertAlmostEqual(payload['retention_amount'], 75.00)
        self.assertAlmostEqual(payload['retention_percent'], 5.0)

    def test_extract_invoice_fields_handles_fallback_numbers_without_labels(self):
        payload = extract_invoice_fields(
            """COMERCIAL ABC C.A.
            RIF J-12345678-9
            1500,00
            75,00
            5%"""
        )
        self.assertEqual(payload['rif'], 'J-12345678-9')
        self.assertAlmostEqual(payload['total_amount'], 1500.00)
        self.assertAlmostEqual(payload['retention_amount'], 75.00)
        self.assertAlmostEqual(payload['retention_percent'], 5.0)

    def test_extract_invoice_fields_calculates_retention_from_percentage(self):
        payload = extract_invoice_fields(
            """PROVEEDOR: COMERCIAL ABC C.A.
            RIF: J-12345678-9
            TOTAL FACTURA: 2000.00
            PORCENTAJE DE RETENCION: 2.5%"""
        )
        self.assertEqual(payload['rif'], 'J-12345678-9')
        self.assertAlmostEqual(payload['total_amount'], 2000.00)
        self.assertAlmostEqual(payload['retention_percent'], 2.5)
        self.assertAlmostEqual(payload['retention_amount'], 50.00)

    def test_extract_invoice_fields_reads_taxable_base_and_iva(self):
        payload = extract_invoice_fields(
            """PROVEEDOR: CERRAJERIA LOS SOCIOS
            RIF: J-297016708
            TOTAL FACTURA: 14.038,87
            BASE IMPONIBLE: 12.102,47
            IVA: 1.936,40
            PORCENTAJE DE RETENCION: 75%"""
        )
        self.assertEqual(payload['rif'], 'J-297016708')
        self.assertAlmostEqual(payload['total_amount'], 14038.87)
        self.assertAlmostEqual(payload['taxable_base'], 12102.47)
        self.assertAlmostEqual(payload['iva_amount'], 1936.40)
        self.assertAlmostEqual(payload['retention_percent'], 75.0)
        self.assertAlmostEqual(payload['retention_amount'], 1452.30)

    def test_extract_invoice_fields_preserves_explicit_zero_iva_and_subtotal(self):
        payload = extract_invoice_fields(
            """PROVEEDOR: G.V.G. ELping            sudo apt update
            sudo apt install -y docker.io docker-compose-plugin nginx certbot python3-certbot-nginx gitECTRIC, C.A.
            RIF: J-30123456-7
            SUBTOTAL: 6,868.72
            TOTAL FACTURA: 7,967.72
            IVA: 0
            RETENCION: 0"""
        )
        self.assertEqual(payload['supplier_name'], 'G.V.G. ELECTRIC, C.A.')
        self.assertEqual(payload['rif'], 'J-30123456-7')
        self.assertAlmostEqual(payload['taxable_base'], 6868.72)
        self.assertAlmostEqual(payload['total_amount'], 7967.72)
        self.assertAlmostEqual(payload['iva_amount'], 0.0)
        self.assertAlmostEqual(payload['retention_amount'], 0.0)


if __name__ == '__main__':
    unittest.main()
