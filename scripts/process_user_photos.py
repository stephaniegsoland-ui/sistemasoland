"""Script to process existing users with `photo_data` (base64), write image files
into `app/static/users/` and update their `photo_path` in the database.

Usage:
  .\env\Scripts\Activate.ps1
  python scripts/process_user_photos.py
"""
import asyncio
import base64
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.db import engine
from app.models.user import User

ROOT = Path(__file__).resolve().parent.parent
STATIC_USERS_DIR = ROOT / "app" / "static" / "users"
STATIC_USERS_DIR.mkdir(parents=True, exist_ok=True)


async def process() -> None:
    async with AsyncSession(engine) as session:
        stmt = select(User).where(User.photo_data != None)
        result = await session.exec(stmt)
        users = result.all()

        if not users:
            print("No users with photo_data found.")
            return

        print(f"Found {len(users)} users with photo_data. Processing...")

        processed = 0
        for user in users:
            pd: Optional[str] = getattr(user, "photo_data", None)
            if not pd:
                continue
            if pd.startswith("data:"):
                try:
                    _, encoded = pd.split(",", 1)
                except ValueError:
                    print(f"Skipping user {user.id}: invalid data url")
                    continue
            else:
                encoded = pd

            try:
                data = base64.b64decode(encoded)
            except Exception as e:
                print(f"Failed to decode user {user.id}: {e}")
                continue

            filename = f"{user.id}.png"
            target = STATIC_USERS_DIR / filename
            try:
                target.write_bytes(data)
            except Exception as e:
                print(f"Failed to write file for user {user.id}: {e}")
                continue

            user.photo_path = f"/static/users/{filename}"
            user.photo_data = None
            session.add(user)
            processed += 1

        await session.commit()
        print(f"Processed {processed} users. Files written to: {STATIC_USERS_DIR}")


if __name__ == "__main__":
    asyncio.run(process())
