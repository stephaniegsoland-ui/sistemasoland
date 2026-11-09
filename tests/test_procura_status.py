import uuid
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select

from app.api.endpoints.procura import update_procura
from app.models.notification import Notification
from app.models.procura import Procura
from app.models.user import User
from app.schemas.procura import ProcuraUpdate


class ProcuraStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Procura.__table__.create)
            await connection.run_sync(Notification.__table__.create)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_status_update_saves_notification_for_uuid_requester(self):
        requester_id = uuid.uuid4()
        admin = User(id=uuid.uuid4(), level=1)

        async with self.session_factory() as session:
            procura = Procura(
                requester_id=requester_id,
                requester_name="requester",
                status="pending",
                items=[],
                attribute={},
            )
            session.add(procura)
            await session.commit()

            updated_procura = await update_procura(
                procura.id,
                ProcuraUpdate(status="approved"),
                session,
                admin,
            )

            notification = (
                await session.execute(select(Notification))
            ).scalar_one()

        self.assertEqual(updated_procura.status, "approved")
        self.assertEqual(notification.user_id, str(requester_id))


if __name__ == "__main__":
    unittest.main()
