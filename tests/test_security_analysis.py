import unittest
from fastapi import HTTPException

from app.api.endpoints.security import (
    REQUIRED_EPP_CLASSES,
    SecurityEppAnalysisResult,
    build_analysis_summary,
    parse_roboflow_predictions,
)


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


class RoboflowEppAnalysisTests(unittest.TestCase):
    def test_predictions_are_limited_to_one_full_body_person_and_face(self):
        predictions = [
            {"class": "person", "confidence": 0.99, "x": 100, "y": 100, "width": 80, "height": 180},
            {"class": "full body person", "confidence": 0.98, "x": 100, "y": 100, "width": 80, "height": 180},
            {"class": "face", "confidence": 0.97, "x": 100, "y": 35, "width": 25, "height": 25},
            {"class": "helmet", "confidence": 0.96, "x": 100, "y": 18, "width": 30, "height": 20},
            {"class": "safety vest", "confidence": 0.96, "x": 100, "y": 75, "width": 45, "height": 60},
            {"class": "safety glasses", "confidence": 0.96, "x": 100, "y": 42, "width": 25, "height": 10},
            {"class": "ear plugs", "confidence": 0.96, "x": 100, "y": 45, "width": 12, "height": 12},
            {"class": "coveralls", "confidence": 0.96, "x": 100, "y": 115, "width": 45, "height": 80},
            {"class": "safety boots", "confidence": 0.96, "x": 100, "y": 185, "width": 30, "height": 20},
            {"class": "helmet", "confidence": 0.99, "x": 290, "y": 20, "width": 30, "height": 20},
        ]

        result = parse_roboflow_predictions(
            {"predictions": predictions},
            image_width=300,
            image_height=220,
            confidence_threshold=0.5,
        )

        self.assertTrue(result.person_detected)
        self.assertTrue(result.face_detected)
        self.assertTrue(result.full_body_detected)
        self.assertEqual(result.missing_items, ["guantes"])
        self.assertTrue(all(result.detected_items[item] for item in REQUIRED_EPP_CLASSES if item != "guantes"))

    def test_rejects_photos_without_confirmed_full_body_and_face(self):
        cases = [
            [{"class": "person", "confidence": 0.99, "x": 50, "y": 50, "width": 40, "height": 90}],
            [
                {"class": "person", "confidence": 0.99, "x": 100, "y": 100, "width": 80, "height": 180},
                {"class": "full body person", "confidence": 0.98, "x": 100, "y": 100, "width": 80, "height": 180},
            ],
        ]

        for predictions in cases:
            with self.subTest(predictions=predictions), self.assertRaises(HTTPException) as error:
                parse_roboflow_predictions(
                    {"predictions": predictions},
                    image_width=300,
                    image_height=220,
                    confidence_threshold=0.5,
                )
            self.assertEqual(error.exception.status_code, 422)
