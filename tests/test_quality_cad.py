from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select
from starlette.datastructures import Headers
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.endpoints import quality_cad
from app.api.endpoints.quality_cad import (
    QualityRecordCreate,
    create_quality_record,
    delete_cad_drawing,
    upload_cad_drawing,
)
from app.models.quality_cad import CADDrawing, QualityRecord
from app.models.user import User


class QualityRecordSchemaTests(unittest.TestCase):
    def test_create_trims_title_and_applies_defaults(self):
        record = QualityRecordCreate(record_type="Auditoría", title="  Revisión anual  ")

        self.assertEqual(record.title, "Revisión anual")
        self.assertEqual(record.status, "Abierta")
        self.assertEqual(record.severity, "Media")

    def test_create_rejects_blank_title(self):
        with self.assertRaises(ValidationError):
            QualityRecordCreate(record_type="Inspección", title="   ")

    def test_create_rejects_unknown_status(self):
        with self.assertRaises(ValidationError):
            QualityRecordCreate(
                record_type="Inspección",
                title="Revisión de área",
                status="Pendiente",
            )


class QualityCADPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = TemporaryDirectory()
        database_path = (Path(self.temp_dir.name) / "quality-cad.db").as_posix()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        async with self.engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.session_factory() as session:
            self.user = User(
                username="quality_test",
                email="quality-test@example.com",
                hashed_password="not-a-real-password",
            )
            session.add(self.user)
            await session.commit()
            await session.refresh(self.user)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp_dir.cleanup()

    async def test_quality_record_survives_session_close_and_reopen(self):
        async with self.session_factory() as session:
            created = await create_quality_record(
                QualityRecordCreate(
                    record_type="No conformidad",
                    title="Inspección de prueba",
                ),
                session,
                self.user,
            )
            record_id = created.id

        async with self.session_factory() as session:
            saved = (await session.execute(
                select(QualityRecord).where(QualityRecord.id == record_id)
            )).scalar_one()
            self.assertEqual(saved.title, "Inspección de prueba")
            self.assertEqual(saved.created_by, self.user.id)

    async def test_cad_file_and_metadata_survive_session_close_and_reopen(self):
        files_dir = Path(self.temp_dir.name) / "cad-files"
        files_dir.mkdir()
        uploaded_file = StarletteUploadFile(
            file=BytesIO(b"cad drawing contents"),
            filename="plan.dwg",
            headers=Headers({"content-type": "application/octet-stream"}),
        )

        with patch.object(quality_cad, "CAD_FILES_DIR", files_dir):
            async with self.session_factory() as session:
                created = await upload_cad_drawing(
                    title="Plano de prueba",
                    drawing_code="PL-001",
                    revision="A",
                    category="Planos",
                    notes=None,
                    file=uploaded_file,
                    session=session,
                    user=self.user,
                )
                drawing_id = created.id

            async with self.session_factory() as session:
                saved = (await session.execute(
                    select(CADDrawing).where(CADDrawing.id == drawing_id)
                )).scalar_one()
                self.assertEqual(saved.drawing_code, "PL-001")
                saved_path = files_dir / saved.stored_filename
                self.assertEqual(saved_path.read_bytes(), b"cad drawing contents")

                await delete_cad_drawing(drawing_id, session, self.user)
                self.assertIsNone(await session.get(CADDrawing, drawing_id))
                self.assertFalse(saved_path.exists())

        await uploaded_file.close()


if __name__ == "__main__":
    unittest.main()
