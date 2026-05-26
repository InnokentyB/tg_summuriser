from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tg_summariser.config import settings


@dataclass(slots=True)
class TelegramChannelPost:
    channel_chat_id: int
    channel_title: str
    channel_username: str | None
    message_id: int
    text: str
    link: str | None


class TelegramUserClient:
    def __init__(self) -> None:
        self.client: Any | None = None

    async def connect(self) -> None:
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            return
        if self.client and self.client.is_connected():
            return
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        if not self.client:
            session: str | StringSession = settings.telegram_session_name
            if settings.telegram_session_string:
                session = StringSession(settings.telegram_session_string)
            self.client = TelegramClient(session, settings.telegram_api_id, settings.telegram_api_hash)
        await self.client.connect()

    async def disconnect(self) -> None:
        if self.client:
            await self.client.disconnect()

    async def get_entity(self, username: str):
        if not self.is_connected():
            await self.connect()
        if not self.is_connected():
            raise RuntimeError("Telegram user client is not connected.")
        return await self.client.get_entity(username)

    def is_connected(self) -> bool:
        return bool(self.client and self.client.is_connected())

    async def iter_recent_channel_posts(
        self, channel_ref: int | str, limit: int = 15
    ) -> list[TelegramChannelPost]:
        if not self.client or not self.client.is_connected():
            raise RuntimeError("Telegram user client is not connected.")
        from telethon.tl.custom.message import Message as TelethonMessage

        entity = await self.client.get_entity(channel_ref)
        posts: list[TelegramChannelPost] = []
        async for message in self.client.iter_messages(entity, limit=limit):
            if not isinstance(message, TelethonMessage):
                continue
            text = (message.message or "").strip()
            if not text:
                continue
            username = getattr(entity, "username", None)
            link = f"https://t.me/{username}/{message.id}" if username else None
            posts.append(
                TelegramChannelPost(
                    channel_chat_id=entity.id,
                    channel_title=getattr(entity, "title", str(channel_ref)),
                    channel_username=username,
                    message_id=message.id,
                    text=text,
                    link=link,
                )
            )
        return posts
