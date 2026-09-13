import os
import base64
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from fastapi import Depends
from fastapi_users.db import SQLAlchemyUserDatabase

from app.models.user import User


def resolve_database_url(raw_url: str | None = None) -> str:
    configured_url = raw_url or os.getenv("DB_URL")
    if not configured_url:
        raise ValueError("DB_URL is not configured")

    use_sqlite_fallback = os.getenv("USE_SQLITE_FALLBACK", "false").lower() in {"1", "true", "yes", "on"}
    if use_sqlite_fallback:
        sqlite_path = Path(__file__).resolve().parent.parent / "soland.db"
        return f"sqlite+aiosqlite:///{sqlite_path.as_posix()}"

    normalized = configured_url.replace("mysql+mysqldb://", "mysql+aiomysql://")
    normalized = normalized.replace("mysql+pymysql://", "mysql+aiomysql://")
    if normalized.startswith("mysql://"):
        normalized = normalized.replace("mysql://", "mysql+aiomysql://", 1)
    parsed = urlparse(normalized)
    if parsed.scheme.startswith("mysql") and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        netloc = parsed.netloc
        if parsed.hostname == "localhost":
            netloc = netloc.replace("localhost", "127.0.0.1", 1)
        elif parsed.hostname == "::1":
            netloc = netloc.replace("::1", "127.0.0.1", 1)
        parsed = parsed._replace(netloc=netloc)
        normalized = urlunparse(parsed)
    return normalized

STATIC_USERS_DIR = Path(__file__).resolve().parent.parent / "static" / "users"
STATIC_USERS_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv()


def normalize_database_url(raw_url: str | None) -> str:
    if not raw_url:
        raise ValueError("DB_URL is not configured")

    normalized = raw_url.replace("mysql+mysqldb://", "mysql+aiomysql://")
    normalized = normalized.replace("mysql+pymysql://", "mysql+aiomysql://")
    if normalized.startswith("mysql://"):
        normalized = normalized.replace("mysql://", "mysql+aiomysql://", 1)
    parsed = urlparse(normalized)
    if parsed.scheme.startswith("mysql") and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        netloc = parsed.netloc
        if parsed.hostname == "localhost":
            netloc = netloc.replace("localhost", "127.0.0.1", 1)
        elif parsed.hostname == "::1":
            netloc = netloc.replace("::1", "127.0.0.1", 1)
        parsed = parsed._replace(netloc=netloc)
        normalized = urlunparse(parsed)
    return normalized


DATABASE_URL = resolve_database_url(os.getenv("DB_URL"))

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)


async def get_async_session():
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_maker() as session:
        yield session


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)


async def repair_legacy_user_photos():
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_maker() as session:
        stmt = text("SELECT id, photo_data FROM `user` WHERE photo_data IS NOT NULL")
        result = await session.execute(stmt)
        rows = result.all()
        repaired = 0
        for row in rows:
            user_id, photo_data = row
            if not photo_data:
                continue
            if photo_data.startswith("data:"):
                try:
                    _, encoded = photo_data.split(",", 1)
                except ValueError:
                    continue
            else:
                encoded = photo_data
            try:
                data = base64.b64decode(encoded)
            except Exception:
                continue
            filename = f"{user_id}.png"
            target = STATIC_USERS_DIR / filename
            try:
                target.write_bytes(data)
            except Exception:
                continue
            await session.execute(
                text(
                    "UPDATE `user` SET photo_path = :photo_path, photo_data = NULL WHERE id = :user_id"
                ),
                {"photo_path": f"/static/users/{filename}", "user_id": user_id},
            )
            repaired += 1
        await session.commit()
        print(f"Repaired {repaired} legacy user photos.")


async def create_db_and_tables():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

            if str(engine.url).startswith("sqlite"):
                return

            async def table_exists(table_name: str) -> bool:
                query = text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name = :table"
                )
                result = await conn.execute(query, {"table": table_name})
                return result.scalar_one() > 0

            async def rename_legacy_chat_table():
                legacy_names = ["chatmessage", "chat_message"]
                existing = [name for name in legacy_names if await table_exists(name)]
                if "chat_message" in existing:
                    return
                if "chatmessage" in existing:
                    try:
                        await conn.exec_driver_sql("ALTER TABLE chatmessage RENAME TO chat_message")
                    except Exception:
                        pass

            await rename_legacy_chat_table()

            async def column_exists(table_name: str, column_name: str) -> bool:
                query = text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :table AND column_name = :column"
                )
                result = await conn.execute(query, {"table": table_name, "column": column_name})
                value = result.scalar_one()
                return value > 0

            async def column_info(table_name: str, column_name: str):
                query = text(
                    "SELECT IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :table AND column_name = :column"
                )
                result = await conn.execute(query, {"table": table_name, "column": column_name})
                return result.one_or_none()

            needed_columns = {
                "procura": {
                    "items": "JSON NOT NULL",
                    "use": "VARCHAR(255) NULL",
                    "requester_id": "VARCHAR(36) NULL",
                    "requester_name": "VARCHAR(255) NULL",
                    "requester_department": "VARCHAR(255) NULL",
                    "status": "VARCHAR(50) NOT NULL DEFAULT 'pending'",
                    "notes": "TEXT NULL",
                    "total_cost": "FLOAT NOT NULL DEFAULT 0.0",
                    "requested_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
                    "updated_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
                    "attribute": "JSON NOT NULL",
                },
                "notification": {
                    "id": "CHAR(32) NOT NULL",
                    "user_id": "VARCHAR(36) NOT NULL",
                    "title": "VARCHAR(255) NOT NULL",
                    "message": "VARCHAR(255) NOT NULL",
                    "payload": "VARCHAR(255) NULL",
                    "type": "VARCHAR(255) NOT NULL DEFAULT 'info'",
                    "is_read": "TINYINT(1) NOT NULL DEFAULT 0",
                    "created_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
                },
                "user": {
                    "department": "VARCHAR(255) NULL",
                    "nombre_completo": "VARCHAR(255) NULL",
                    "cargo": "VARCHAR(255) NULL",
                    "hoja_vida": "TEXT NULL",
                    "photo_data": "LONGTEXT NULL",
                    "photo_path": "VARCHAR(255) NULL",
                    "permissions": "JSON NULL",
                    "avatar_config": "JSON NULL",
                },
                "vehicle_inspection": {
                    "report": "TEXT NOT NULL",
                    "notes": "TEXT NULL",
                    "before_images": "JSON NULL",
                    "after_images": "JSON NULL",
                    "fuel_level": "VARCHAR(255) NULL",
                    "tire_condition": "VARCHAR(255) NULL",
                    "summary_tags": "JSON NULL",
                    "pdf_file": "VARCHAR(255) NULL",
                },
                "security_epp_report": {
                    "summary": "TEXT NOT NULL",
                    "recommendations": "TEXT NULL",
                    "thumbnail_path": "VARCHAR(255) NULL",
                },
                "safety_permit": {
                    "extracted_text": "TEXT NOT NULL",
                },
                "chat_message": {
                    "recipient_id": "CHAR(32) NULL",
                    "message_type": "VARCHAR(50) NOT NULL DEFAULT 'info'",
                    "reference_title": "VARCHAR(255) NULL",
                    "reference_url": "VARCHAR(500) NULL",
                },
                "invoice_retentions": {
                    "taxable_base": "FLOAT NOT NULL DEFAULT 0.0",
                    "iva_amount": "FLOAT NOT NULL DEFAULT 0.0",
                },
            }

            for table, columns in needed_columns.items():
                for column, ddl_type in columns.items():
                    if not await column_exists(table, column):
                        try:
                            await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
                        except Exception:
                            pass
                    elif table == "vehicle_inspection" and column == "notes":
                        info = await column_info(table, column)
                        if info and info[0] == "NO":
                            try:
                                await conn.exec_driver_sql(
                                    f"ALTER TABLE {table} MODIFY COLUMN {column} TEXT NULL"
                                )
                            except Exception:
                                pass
                    elif table == "vehicle_inspection" and column == "report":
                        try:
                            await conn.exec_driver_sql(
                                "ALTER TABLE vehicle_inspection MODIFY COLUMN report TEXT NOT NULL"
                            )
                        except Exception:
                            pass
                    elif table == "security_epp_report" and column == "summary":
                        try:
                            await conn.exec_driver_sql(
                                "ALTER TABLE security_epp_report MODIFY COLUMN summary TEXT NOT NULL"
                            )
                        except Exception:
                            pass
                    elif table == "security_epp_report" and column == "recommendations":
                        try:
                            await conn.exec_driver_sql(
                                "ALTER TABLE security_epp_report MODIFY COLUMN recommendations TEXT NULL"
                            )
                        except Exception:
                            pass
                    elif table == "safety_permit" and column == "extracted_text":
                        try:
                            await conn.exec_driver_sql(
                                "ALTER TABLE safety_permit MODIFY COLUMN extracted_text TEXT NOT NULL"
                            )
                        except Exception:
                            pass

            if await column_exists("vehicle_inspection", "username"):
                info = await column_info("vehicle_inspection", "username")
                if info and info[0] == "NO":
                    try:
                        await conn.exec_driver_sql(
                            "ALTER TABLE vehicle_inspection MODIFY COLUMN username VARCHAR(255) NULL"
                        )
                    except Exception:
                        pass

            if await column_exists("user", "photo_data"):
                try:
                    query = text(
                        "SELECT DATA_TYPE FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() AND table_name = :table AND column_name = :column"
                    )
                    result = await conn.execute(query, {"table": "user", "column": "photo_data"})
                    row = result.one_or_none()
                    if row:
                        data_type = (row[0] or "").lower()
                        if data_type != "longtext":
                            try:
                                await conn.exec_driver_sql(
                                    "ALTER TABLE user MODIFY COLUMN photo_data LONGTEXT NULL"
                                )
                            except Exception:
                                pass
                except Exception:
                    pass
    except Exception as exc:
        print(f"Database initialization skipped: {exc}")

