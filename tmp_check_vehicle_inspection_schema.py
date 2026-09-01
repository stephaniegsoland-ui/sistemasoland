import asyncio
from app.core.db import engine

async def main():
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql('SHOW COLUMNS FROM vehicle_inspection')
        for row in result:
            print(row)

if __name__ == '__main__':
    asyncio.run(main())
