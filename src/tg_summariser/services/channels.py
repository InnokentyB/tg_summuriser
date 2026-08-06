from __future__ import annotations

import re

from aiogram.types import Message

from tg_summariser.models import Channel
from tg_summariser.services.repositories import ChannelRepository
from tg_summariser.services.telegram_client import TelegramUserClient


class ChannelService:
    def __init__(self, tg_client: TelegramUserClient) -> None:
        self.tg_client = tg_client

    async def add_from_forward(self, message: Message, repo: ChannelRepository) -> Channel:
        if not message.forward_from_chat:
            raise ValueError("Перешлите именно пост из канала.")

        chat = message.forward_from_chat
        channel = await repo.upsert_channel(
            telegram_chat_id=chat.id,
            title=chat.title or chat.full_name or "Untitled channel",
            telegram_username=chat.username,
            is_private=not bool(chat.username),
        )
        return channel

    async def add_from_text(self, text: str, repo: ChannelRepository) -> Channel:
        usernames = extract_channel_usernames(text)
        if not usernames:
            raise ValueError("Не удалось распознать username канала.")
        username = usernames[0]
        entity = await self.tg_client.get_entity(username)
        if not isinstance(getattr(entity, "id", None), int):
            raise ValueError("Не удалось получить данные канала.")
        title = getattr(entity, "title", username)
        channel = await repo.upsert_channel(
            telegram_chat_id=entity.id,
            title=title,
            telegram_username=username,
            is_private=False,
        )
        return channel


def extract_channel_usernames(text: str) -> list[str]:
    usernames: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?:https?://t\.me/|@)([A-Za-z0-9_]+)", text):
        username = match.group(1)
        normalized = username.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        usernames.append(username)
    return usernames
