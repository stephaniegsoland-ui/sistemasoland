import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.db import engine
from app.core.notifications import broadcast_notification
from app.models.notification import Notification
from app.models.settings import SystemSettings
from app.models.user import User
from app.api.endpoints.documents import ScannedDocument

logger = logging.getLogger("soland.document_reminders")


async def send_document_expiry_reminders() -> int:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        users = (await session.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
        documents = (await session.execute(select(ScannedDocument).where(ScannedDocument.expiration_date.is_not(None)))).scalars().all()
        system_settings = await session.get(SystemSettings, "general")
        default_days = set(system_settings.expiry_reminder_days if system_settings else [30, 7, 1])
        new_notifications: list[tuple[Notification, User]] = []

        for user in users:
            user_settings = user.settings or {}
            preferences = user_settings.get("preferences", {})
            if not preferences.get("notifications_enabled", True):
                continue
            configured_days = preferences.get("document_expiry_days")
            reminder_days = set(configured_days) if configured_days is not None else default_days
            if not reminder_days:
                continue

            try:
                timezone = ZoneInfo(preferences.get("timezone", system_settings.default_timezone if system_settings else "America/Caracas"))
            except ZoneInfoNotFoundError:
                timezone = ZoneInfo("UTC")
            today = datetime.now(timezone).date()
            existing_payloads = set((await session.execute(
                select(Notification.payload).where(Notification.user_id == str(user.id), Notification.payload.like("doc-expiry|%"))
            )).scalars().all())

            for document in documents:
                days_remaining = (document.expiration_date - today).days
                if days_remaining not in reminder_days:
                    continue
                deduplication_key = f"doc-expiry|{document.id}|{document.expiration_date.isoformat()}|{days_remaining}"
                if deduplication_key in existing_payloads:
                    continue
                notification = Notification(
                    user_id=str(user.id),
                    title="Documento próximo a vencer",
                    message=f"{document.original_filename} vence en {days_remaining} día(s).",
                    type="info",
                    payload=deduplication_key,
                )
                session.add(notification)
                new_notifications.append((notification, user))
                existing_payloads.add(deduplication_key)

        if not new_notifications:
            return 0
        await session.commit()
        for notification, user in new_notifications:
            await session.refresh(notification)
            try:
                broadcast_notification(user.id, {
                    "id": notification.id,
                    "user_id": notification.user_id,
                    "title": notification.title,
                    "message": notification.message,
                    "type": notification.type,
                    "read": notification.is_read,
                    "created_at": notification.created_at.isoformat(),
                })
            except Exception:
                logger.exception("Could not broadcast document reminder %s", notification.id)
        return len(new_notifications)


async def document_expiry_reminder_loop() -> None:
    while True:
        try:
            count = await send_document_expiry_reminders()
            if count:
                logger.info("Sent %s document expiry reminders", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Document expiry reminder run failed")
        await asyncio.sleep(60 * 60)