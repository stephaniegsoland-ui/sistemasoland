from app.api.endpoints.safety_permits import extract_permit_date, extract_permit_number, extract_permit_time, field_from_text


def test_extract_permit_number_accepts_common_number_labels():
    assert extract_permit_number("N° permiso: 12345") == "12345"
    assert extract_permit_number("Número de permiso - PI-202501") == "PI-202501"


def test_extract_permit_date_accepts_date_without_word_boundaries():
    assert extract_permit_date("Fecha: 08/03/2026") == "08/03/2026"
    assert extract_permit_date("Fecha del permiso: 8-3-26") == "8-3-26"


def test_extract_permit_time_normalizes_compact_ocr_values():
    assert extract_permit_time("Hora de inicio: 030") == "03:00"
    assert extract_permit_time("Hora: 0330") == "03:30"
    assert extract_permit_time("Hora de inicio: 030 AM") == "03:00 AM"
    assert extract_permit_time("Hora: 0330 p.m.") == "03:30 PM"
    assert extract_permit_time("Hora: 030 a. m.") == "03:00 AM"
    assert extract_permit_time("Hora: 0330 p. m.") == "03:30 PM"
    assert extract_permit_time("Hora: 511") == "05:11"


def test_field_from_text_reads_exact_value_on_following_ocr_line():
    text = "ACTIVIDAD\nInstalación de soporte en PTO DE DRENAJE\nÁREA DE TRABAJO\nPTO DE DRENAJE"
    assert field_from_text(text, ["actividad"]) == "Instalación de soporte en PTO DE DRENAJE"
    assert field_from_text(text, ["área de trabajo", "area de trabajo"]) == "PTO DE DRENAJE"


def test_field_from_text_stops_before_numbered_section_header():
    text = "DESCRIPCIÓN DE LOS TRABAJOS\nInstalación de cables\n8.- ANÁLISIS DE RIESGOS"
    assert field_from_text(text, ["descripción de los trabajos"]) == "Instalación de cables"