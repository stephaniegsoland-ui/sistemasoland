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
