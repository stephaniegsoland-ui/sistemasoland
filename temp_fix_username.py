from dotenv import load_dotenv
load_dotenv()
import os
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def run():
    database_url = os.getenv('DB_URL')
    if not database_url:
        raise RuntimeError('DB_URL is not set in environment')
    database_url = database_url.replace('pymysql', 'aiomysql')
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text('ALTER TABLE vehicle_inspection MODIFY COLUMN username VARCHAR(255) NULL'))
    await engine.dispose()
    print('vehicle_inspection.username set to NULL')

if __name__ == '__main__':
    asyncio.run(run())
