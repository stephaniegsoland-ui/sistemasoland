import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.core.db import engine
from app.models.user import User
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

async def main():
    async with AsyncSession(engine) as session:
        res = await session.execute(select(User).limit(10))
        users = res.scalars().all()
        print('users', len(users))
        for u in users:
            print('id', u.id, 'username', u.username, 'email', u.email, 'level', getattr(u,'level',None), 'is_superuser', getattr(u,'is_superuser',None))

asyncio.run(main())
