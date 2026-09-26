from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from tg_summariser.config import settings

if TYPE_CHECKING:
    from telethon import TelegramClient


@dataclass(slots=True)
class TelegramChannelPost:
    """Representation of an ingested Telegram channel message."""

    channel_chat_id: int
    channel_title: str
    channel_username: str | None
    message_id: int
    text: str
    link: str | None
    published_at: datetime | None = None


class TelegramUserClient:
    """Telethon client wrapper for accessing Telegram channels and messages."""

    def __init__(self) -> None:
        """Initialize client holder."""
        self.client: TelegramClient | None = None

    async def connect(self) -> None:
        """Establish Telethon session connection if credentials are configured."""
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
        """Disconnect active Telethon session."""
        if self.client:
            await self.client.disconnect()

    async def get_entity(self, username: str) -> object:
        """Resolve a Telegram username or channel reference to a Telethon entity."""
        if not self.is_connected():
            await self.connect()
        if not self.is_connected() or not self.client:
            raise RuntimeError("Telegram user client is not connected.")
        return await self.client.get_entity(username)

    def is_connected(self) -> bool:
        """Return True if the underlying client is connected and active."""
        return bool(self.client and self.client.is_connected())

    async def iter_recent_channel_posts(
        self, channel_ref: int | str, limit: int = 15
    ) -> list[TelegramChannelPost]:
        """Fetch recent channel messages and convert them to TelegramChannelPost."""
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
                    published_at=message.date.astimezone(timezone.utc).replace(tzinfo=None),
                )
            )
        return posts

    async def mark_channel_posts_read(self, channel_ref: int | str, max_message_id: int) -> None:
        """Mark posts up to max_message_id as read in the specified channel."""
        if not self.client or not self.client.is_connected():
            raise RuntimeError("Telegram user client is not connected.")
        entity = await self.client.get_entity(channel_ref)
        await self.client.send_read_acknowledge(entity, max_id=max_message_id, clear_mentions=True)
