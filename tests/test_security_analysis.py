from app.api.endpoints.security import SecurityEppAnalysisResult, build_analysis_summary


def test_build_analysis_summary_mentions_missing_epp_and_registration_warning():
    analysis = SecurityEppAnalysisResult(
        person_detected=True,
        detected_items={
            "cascos": True,
            "chaleco": False,
            "guantes": False,
            "lentes de seguridad": False,
            "tapones de oído": False,
            "braga de seguridad": False,
            "botas": False,
        },
        present_items=["cascos"],
        missing_items=["chaleco", "botas"],
        score=60.0,
        summary="",
        recommendations=[],
    )

    summary = build_analysis_summary(analysis, None, None, None)

    assert "No se encontró coincidencia con un operador registrado" in summary
    assert "Faltan EPP" in summary
    assert "chaleco" in summary
    assert "botas" in summary


def test_build_analysis_summary_mentions_identified_operator():
    analysis = SecurityEppAnalysisResult(
        person_detected=True,
        detected_items={
            "cascos": True,
            "chaleco": True,
            "guantes": True,
            "lentes de seguridad": True,
            "tapones de oído": True,
            "braga de seguridad": True,
            "botas": True,
        },
        present_items=["cascos", "chaleco", "guantes", "lentes de seguridad", "tapones de oído", "braga de seguridad", "botas"],
        missing_items=[],
        score=100.0,
        summary="",
        recommendations=[],
    )

    summary = build_analysis_summary(analysis, "juan", 0.91, None)

    assert "Operador identificado como juan" in summary
    assert "No faltan EPP detectados" in summary
