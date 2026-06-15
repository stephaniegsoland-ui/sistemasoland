import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from fastapi import Depends
from fastapi_users.db import SQLAlchemyUserDatabase

from app.models.user import User

load_dotenv()

DATABASE_URL = os.getenv("DB_URL").replace("pymysql", "aiomysql")

engine = create_async_engine(DATABASE_URL, echo=False)


async def get_async_session():
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_maker() as session:
        yield session


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)


async def create_db_and_tables():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        for ddl in [
            "ALTER TABLE procura ADD COLUMN items JSON NOT NULL DEFAULT '[]'",
            "ALTER TABLE procura ADD COLUMN `use` VARCHAR(255) NULL",
            "ALTER TABLE procura ADD COLUMN requester_department VARCHAR(255) NULL",
            "ALTER TABLE `user` ADD COLUMN department VARCHAR(255) NULL",
        ]:
            try:
                await conn.exec_driver_sql(ddl)
            except Exception:
                # La columna ya existe o no es posible modificarla; no detenemos el arranque.
                pass
