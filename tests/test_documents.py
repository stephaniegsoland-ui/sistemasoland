from datetime import date

from app.api.endpoints.documents import extract_expiration_date, identify_document_type


def test_extract_expiration_date_requires_expiration_label():
    assert extract_expiration_date("Fecha de emisión: 01/02/2026") is None


def test_extract_expiration_date_reads_numeric_date_after_label():
    assert extract_expiration_date("Vence: 31/12/2027") == date(2027, 12, 31)


def test_extract_expiration_date_reads_date_on_following_ocr_line():
    assert extract_expiration_date("VIGENCIA HASTA\n15-08-2028") == date(2028, 8, 15)


def test_extract_expiration_date_reads_spanish_month_name():
    assert extract_expiration_date("Fecha de vencimiento: 4 de octubre de 2027") == date(2027, 10, 4)


def test_extract_expiration_date_ignores_invalid_dates():
    assert extract_expiration_date("Caducidad: 31/02/2027") is None


def test_identify_document_type_from_scanned_text_or_filename():
    assert identify_document_type("Certificado de salud ocupacional") == "Certificado"
    assert identify_document_type("", "licencia-conducir.pdf") == "Licencia"
    assert identify_document_type("Texto sin categoría") == "No identificado"