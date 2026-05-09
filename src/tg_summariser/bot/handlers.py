from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from tg_summariser.bot.keyboards import feedback_keyboard
from tg_summariser.config import settings
from tg_summariser.db import session_scope
from tg_summariser.models import FeedbackValue
from tg_summariser.services.channels import ChannelService
from tg_summariser.services.digest_service import DigestService
from tg_summariser.services.ingestion import IngestionService
from tg_summariser.services.post_processor import PostProcessor
from tg_summariser.services.repositories import (
    ChannelRepository,
    FeedbackRepository,
    PostRepository,
    UserRepository,
)
from tg_summariser.services.ai_pipeline import AIPipeline
from tg_summariser.services.dedup import Deduplicator
from tg_summariser.services.scoring import RelevanceScorer

router = Router()


def parse_search_args(raw_args: str) -> tuple[str, str | None, str | None]:
    query = raw_args.strip()
    category = None
    channel = None

    if ";" not in raw_args:
        return query, category, channel

    parts = [part.strip() for part in raw_args.split(";") if part.strip()]
    if parts:
        query = parts[0]
    for part in parts[1:]:
        if part.lower().startswith("category="):
            category = part.split("=", maxsplit=1)[1].strip() or None
        elif part.lower().startswith("channel="):
            channel = part.split("=", maxsplit=1)[1].strip() or None
    return query, category, channel


def register_handlers(channel_service: ChannelService, ingestion_service: IngestionService) -> Router:
    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if settings.owner_telegram_id and message.from_user and message.from_user.id != settings.owner_telegram_id:
            await message.answer("Этот MVP пока доступен только владельцу.")
            return

        async with session_scope() as session:
            repo = UserRepository(session)
            await repo.get_or_create(message.from_user.id, message.from_user.username if message.from_user else None)
        await message.answer(
            "Бот запущен. Перешлите пост из канала, чтобы добавить источник, или используйте /help."
        )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(
            "/add - инструкция по добавлению канала\n"
            "/channels - список каналов\n"
            "/digest - собрать дайджест сейчас\n"
            "/hidden - показать скрытые посты\n"
            "/search <запрос> - поиск по истории"
        )

    @router.message(Command("add"))
    async def add_command(message: Message) -> None:
        await message.answer("Перешлите пост из канала сюда или отправьте ссылку вида https://t.me/channel_name.")

    @router.message(Command("channels"))
    async def channels_command(message: Message) -> None:
        async with session_scope() as session:
            channels = await ChannelRepository(session).list_channels()
        if not channels:
            await message.answer("Пока нет подключенных каналов.")
            return
        await message.answer("\n".join(f"• {channel.title}" for channel in channels))

    @router.message(Command("hidden"))
    async def hidden_command(message: Message) -> None:
        async with session_scope() as session:
            posts = await PostRepository(session).hidden_posts()
        if not posts:
            await message.answer("Скрытых постов пока нет.")
            return
        for post in posts:
            await message.answer(
                f"{post.summary or post.raw_text[:180]}\n"
                f"Причина: {post.explanation or 'Без пояснения'}\n"
                f"Ссылка: {post.original_link or 'Нет ссылки'}",
                reply_markup=feedback_keyboard(post.id),
                disable_web_page_preview=True,
            )

    @router.message(Command("digest"))
    async def digest_command(message: Message) -> None:
        if not message.from_user:
            return
        async with session_scope() as session:
            user = await UserRepository(session).get_or_create(
                message.from_user.id, message.from_user.username
            )
            synced = await ingestion_service.sync_channels(session)
            processor = PostProcessor(AIPipeline(), Deduplicator(), RelevanceScorer())
            processed = await processor.process_pending(session, user.id)
            sent = await DigestService(message.bot).send_digest(session, user.id, message.from_user.id)
        await message.answer(
            f"Дайджест собран.\n"
            f"Новых постов синхронизировано: {synced}\n"
            f"Обработано AI: {processed}\n"
            f"Отправлено в дайджест: {sent}"
        )

    @router.message(Command("search"))
    async def search_command(message: Message, command: CommandObject) -> None:
        query, category, channel = parse_search_args(command.args or "")
        if not query:
            await message.answer(
                "Добавьте запрос: /search ai agents\n"
                "Фильтры: /search ai agents; category=AI & Agents; channel=Some Channel"
            )
            return
        async with session_scope() as session:
            posts = await PostRepository(session).search(
                query=query,
                category=category,
                channel=channel,
            )
        if not posts:
            await message.answer("Ничего не нашлось.")
            return
        for post in posts:
            await message.answer(
                f"{post.summary or post.raw_text[:180]}\n"
                f"Категория: {post.category or 'Без категории'}\n"
                f"Источник: {post.channel.title}\n"
                f"Ссылка: {post.original_link or 'Нет ссылки'}",
                reply_markup=feedback_keyboard(post.id),
                disable_web_page_preview=True,
            )

    @router.message(F.forward_from_chat)
    async def add_forwarded_channel(message: Message) -> None:
        if not message.from_user:
            return
        async with session_scope() as session:
            repo = ChannelRepository(session)
            user = await UserRepository(session).get_or_create(
                message.from_user.id, message.from_user.username
            )
            try:
                channel = await channel_service.add_from_forward(message, repo)
            except ValueError as exc:
                await message.answer(str(exc))
                return
            synced = await ingestion_service.sync_channel(session, channel)
            processor = PostProcessor(AIPipeline(), Deduplicator(), RelevanceScorer())
            processed = await processor.process_pending(session, user.id)
            sent = await DigestService(message.bot).send_channel_welcome_digest(
                session=session,
                user_id=user.id,
                telegram_id=message.from_user.id,
                channel_id=channel.id,
                channel_title=channel.title,
            )
        await message.answer(
            f"Канал '{channel.title}' добавлен в отслеживание.\n"
            f"Импортировано постов: {synced}\n"
            f"Обработано AI: {processed}\n"
            f"Отправлено в стартовое саммари: {sent}"
        )

    @router.message(F.text.regexp(r"(https?://t\.me/|@)"))
    async def add_channel_by_text(message: Message) -> None:
        if not message.from_user:
            return
        async with session_scope() as session:
            repo = ChannelRepository(session)
            user = await UserRepository(session).get_or_create(
                message.from_user.id, message.from_user.username
            )
            try:
                channel = await channel_service.add_from_text(message.text or "", repo)
            except Exception as exc:
                await message.answer(f"Не удалось добавить канал: {exc}")
                return
            synced = await ingestion_service.sync_channel(session, channel)
            processor = PostProcessor(AIPipeline(), Deduplicator(), RelevanceScorer())
            processed = await processor.process_pending(session, user.id)
            sent = await DigestService(message.bot).send_channel_welcome_digest(
                session=session,
                user_id=user.id,
                telegram_id=message.from_user.id,
                channel_id=channel.id,
                channel_title=channel.title,
            )
        await message.answer(
            f"Канал '{channel.title}' добавлен в отслеживание.\n"
            f"Импортировано постов: {synced}\n"
            f"Обработано AI: {processed}\n"
            f"Отправлено в стартовое саммари: {sent}"
        )

    @router.callback_query(F.data.startswith("feedback:"))
    async def feedback_callback(callback: CallbackQuery) -> None:
        _, post_id_str, value_str = (callback.data or "").split(":")
        feedback_value = FeedbackValue(value_str)
        if not callback.from_user:
            await callback.answer("Не удалось определить пользователя.", show_alert=True)
            return

        async with session_scope() as session:
            user = await UserRepository(session).get_or_create(
                callback.from_user.id, callback.from_user.username
            )
            post = await PostRepository(session).get(int(post_id_str))
            if not post:
                await callback.answer("Пост не найден.", show_alert=True)
                return
            await FeedbackRepository(session).add_feedback(user.id, post.id, feedback_value)
            if feedback_value == FeedbackValue.not_interested:
                post.relevance_score = max(0.0, post.relevance_score - 0.25)
            else:
                post.relevance_score = min(1.0, post.relevance_score + 0.15)
            await callback.answer("Оценка сохранена.")
            if callback.message:
                await callback.message.edit_reply_markup(reply_markup=None)

    return router
