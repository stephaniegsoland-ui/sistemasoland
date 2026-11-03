import asyncio
import io
import unittest

from fastapi import HTTPException
from fastapi import UploadFile

from app.core.auth import get_supervisor_or_admin
from app.core.storage import read_upload, safe_filename_suffix
from app.models.user import User


class ProductionHardeningTests(unittest.TestCase):
    def test_regular_user_is_rejected_from_admin_dependency(self):
        user = User(level=3, is_superuser=False)
        with self.assertRaises(HTTPException) as error:
            asyncio.run(get_supervisor_or_admin(user))
        self.assertEqual(error.exception.status_code, 403)

    def test_supervisor_is_allowed_by_admin_dependency(self):
        user = User(level=2, is_superuser=False)
        self.assertIs(asyncio.run(get_supervisor_or_admin(user)), user)

    def test_upload_reader_rejects_oversized_files(self):
        upload = UploadFile(
            filename="receipt.jpg",
            file=io.BytesIO(b"0123456789"),
            headers={"content-type": "image/jpeg"},
        )
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                read_upload(
                    upload,
                    allowed_extensions={".jpg"},
                    allowed_content_types={"image/jpeg"},
                    max_bytes=5,
                )
            )
        self.assertEqual(error.exception.status_code, 413)

    def test_upload_reader_accepts_allowed_file(self):
        upload = UploadFile(
            filename="receipt.jpg",
            file=io.BytesIO(b"valid"),
            headers={"content-type": "image/jpeg"},
        )
        content = asyncio.run(
            read_upload(
                upload,
                allowed_extensions={".jpg"},
                allowed_content_types={"image/jpeg"},
                max_bytes=5,
            )
        )
        self.assertEqual(content, b"valid")
        self.assertEqual(safe_filename_suffix(upload.filename, ".jpg"), ".jpg")


if __name__ == "__main__":
    unittest.main()
