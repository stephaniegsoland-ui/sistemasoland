import asyncio
from app.core.db import engine
from app.models.user import User
from app.models.vehicle import Vehicle
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

async def main():
    async with AsyncSession(engine) as session:
        users = (await session.execute(select(User).limit(10))).scalars().all()
        vehicles = (await session.execute(select(Vehicle).limit(10))).scalars().all()
        print('users', len(users))
        for u in users:
            print('user', u.username, u.email, u.level, u.is_superuser)
        print('vehicles', len(vehicles))
        for v in vehicles:
            print('vehicle', str(v.id), v.license_plate, v.model, v.user_id)

asyncio.run(main())
