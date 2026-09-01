import asyncio
import typing
import uuid
from typing import Dict, List

NotificationPayload = typing.Dict[str, typing.Any]

_notifications_queues: Dict[str, List[asyncio.Queue[NotificationPayload]]] = {}


def get_notification_queues(user_id: uuid.UUID) -> List[asyncio.Queue[NotificationPayload]]:
    return _notifications_queues.setdefault(str(user_id), [])


def register_notification_queue(user_id: uuid.UUID, queue: asyncio.Queue[NotificationPayload]) -> None:
    queues = get_notification_queues(user_id)
    queues.append(queue)


def unregister_notification_queue(user_id: uuid.UUID, queue: asyncio.Queue[NotificationPayload]) -> None:
    queues = get_notification_queues(user_id)
    if queue in queues:
        queues.remove(queue)


def broadcast_notification(user_id: uuid.UUID, payload: NotificationPayload) -> None:
    queues = get_notification_queues(user_id)
    for queue in queues:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass
