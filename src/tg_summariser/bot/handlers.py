from __future__ import annotations

import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from tg_summariser.bot.commands import HELP_TEXT
from tg_summariser.bot.keyboards import feedback_keyboard
from tg_summariser.config import settings
from tg_summariser.db import session_scope
from tg_summariser.models import FeedbackValue
from tg_summariser.services.channels import ChannelService, extract_channel_usernames
from tg_summariser.services.channel_onboarding_queue import ChannelOnboardingQueue
from tg_summariser.services.digest_service import DigestService
from tg_summariser.services.ingestion import IngestionService
from tg_summariser.services.openai_batch import OpenAIBatchService
from tg_summariser.services.post_processor import PostProcessor
from tg_summariser.services.repositories import (
    ChannelOnboardingJobRepository,
    ChannelRepository,
    FeedbackRepository,
    PostRepository,
    UserRepository,
    UserCategoryPreferenceRepository,
)
from tg_summariser.services.ai_pipeline import AIPipeline
from tg_summariser.services.dedup import Deduplicator
from tg_summariser.services.scoring import RelevanceScorer
from tg_summariser.services.tgarticles_importer import TGArticlesImportService

router = Router()
logger = logging.getLogger(__name__)


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


def format_channel_sync_status(last_synced_at: datetime | None, now: datetime | None = None) -> str:
    if last_synced_at is None:
        return "ещё не читали"

    current_time = now or datetime.utcnow()
    elapsed_seconds = max(int((current_time - last_synced_at).total_seconds()), 0)
    elapsed_minutes = elapsed_seconds // 60
    if elapsed_minutes < 1:
        return "читали только что"
    if elapsed_minutes < 60:
        return f"читали {elapsed_minutes} мин назад"

    elapsed_hours = elapsed_minutes // 60
    if elapsed_hours < 24:
        return f"читали {elapsed_hours} ч назад"

    elapsed_days = elapsed_hours // 24
    return f"читали {elapsed_days} д назад"


async def safe_callback_answer(
    callback: CallbackQuery, text: str, *, show_alert: bool = False
) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if "query is too old" in message or "query id is invalid" in message:
            logger.info("Skipped stale callback answer: %s", exc)
            return
        raise


def register_handlers(
    channel_service: ChannelService,
    ingestion_service: IngestionService,
    onboarding_queue: ChannelOnboardingQueue,
) -> Router:
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
        await message.answer(HELP_TEXT)

    @router.message(Command("add"))
    async def add_command(message: Message) -> None:
        await message.answer(
            "Перешлите пост из канала сюда, отправьте ссылку вида https://t.me/channel_name "
            "или используйте /add_many со списком @channel."
        )

    @router.message(Command("add_many"))
    async def add_many_command(message: Message, command: CommandObject) -> None:
        if not message.from_user:
            return
        usernames = extract_channel_usernames(command.args or "")
        if not usernames:
            await message.answer(
                "Пришлите список каналов после команды, например:\n"
                "/add_many @channel_one\n@channel_two"
            )
            return

        added = 0
        queued = 0
        failed: list[str] = []
        for username in usernames:
            async with session_scope() as session:
                repo = ChannelRepository(session)
                try:
                    channel = await channel_service.add_from_text(f"@{username}", repo)
                    added += 1
                except Exception as exc:
                    failed.append(f"@{username}: {type(exc).__name__}: {str(exc)[:120]}")
                    continue

            if await onboarding_queue.enqueue(channel.id, message.from_user.id):
                queued += 1

        lines = [
            "Массовое добавление завершено.",
            f"Найдено username: {len(usernames)}",
            f"Добавлено/обновлено каналов: {added}",
            f"Поставлено в очередь: {queued}",
        ]
        if failed:
            lines.append("")
            lines.append("Ошибки:")
            lines.extend(f"• {item}" for item in failed[:10])
            if len(failed) > 10:
                lines.append(f"• ...и ещё {len(failed) - 10}")
        await message.answer("\n".join(lines))

    @router.message(Command("channels"))
    async def channels_command(message: Message) -> None:
        async with session_scope() as session:
            channel_repo = ChannelRepository(session)
            channels = await channel_repo.list_channels()
            total_telegram_channels = await channel_repo.count_telegram_channels()
        if not channels:
            await message.answer("Пока нет подключенных каналов.")
            return

        lines = [
            f"Каналы: {len(channels)}",
            (
                "Telegram-каналов: "
                f"{total_telegram_channels}. "
                "В каждом дайджесте бот обходит все активные каналы "
                f"с паузой {settings.telegram_sync_delay_seconds:g} сек."
            ),
            "",
        ]
        for channel in channels:
            if channel.source_kind == "telegram_channel":
                status = format_channel_sync_status(channel.last_synced_at)
            else:
                status = "внешний источник"
            lines.append(f"• {channel.title} — {status}")
        await message.answer("\n".join(lines))

    @router.message(Command("categories"))
    async def categories_command(message: Message) -> None:
        if not message.from_user:
            return
        async with session_scope() as session:
            user = await UserRepository(session).get_or_create(
                message.from_user.id, message.from_user.username
            )
            category_repo = UserCategoryPreferenceRepository(session)
            known_categories = await category_repo.known_categories()
            enabled_categories = set(await category_repo.enabled_categories(user.id))

        if not known_categories:
            await message.answer("Категории пока не накопились. Сначала дайте боту обработать несколько постов.")
            return

        lines = ["Категории для дайджеста:"]
        if enabled_categories:
            lines.append("Режим: фильтр по включенным категориям")
        else:
            lines.append("Режим: все категории включены")

        for category in known_categories:
            marker = "ON" if not enabled_categories or category in enabled_categories else "OFF"
            lines.append(f"• [{marker}] {category}")

        lines.append("")
        lines.append("Команды:")
        lines.append("/category_on <категория>")
        lines.append("/category_off <категория>")
        lines.append("/category_reset")
        await message.answer("\n".join(lines))

    @router.message(Command("category_on"))
    async def category_on_command(message: Message, command: CommandObject) -> None:
        await _update_category_filter(message, command, is_enabled=True)

    @router.message(Command("category_off"))
    async def category_off_command(message: Message, command: CommandObject) -> None:
        await _update_category_filter(message, command, is_enabled=False)

    @router.message(Command("category_reset"))
    async def category_reset_command(message: Message) -> None:
        if not message.from_user:
            return
        async with session_scope() as session:
            user = await UserRepository(session).get_or_create(
                message.from_user.id, message.from_user.username
            )
            removed = await UserCategoryPreferenceRepository(session).clear(user.id)
        await message.answer(
            f"Фильтр категорий сброшен. Удалено настроек: {removed}. Теперь дайджест снова показывает все категории."
        )

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
                reply_markup=feedback_keyboard(post),
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
            await OpenAIBatchService().collect_completed(session, user.id)
            synced = await ingestion_service.sync_channels(session)
            article_importer = TGArticlesImportService.from_settings()
            imported_articles = 0
            if article_importer:
                imported_articles = await article_importer.import_recent(session)
            processor = PostProcessor(AIPipeline(), Deduplicator(), RelevanceScorer())
            processed = await processor.process_pending(session, user.id)
            sent = await DigestService(message.bot).send_digest(session, user.id, message.from_user.id)
        await message.answer(
            f"Дайджест собран.\n"
            f"Новых постов синхронизировано: {synced}\n"
            f"Новых статей импортировано: {imported_articles}\n"
            f"Обработано AI: {processed}\n"
            f"Отправлено в дайджест: {sent}"
        )

    @router.message(Command("process_channels"))
    async def process_channels_command(message: Message) -> None:
        if not message.from_user:
            return
        async with session_scope() as session:
            channels = await ChannelRepository(session).channels_without_posts()
            failed_jobs = await ChannelOnboardingJobRepository(session).failed_jobs()

        queued = 0
        seen_channel_ids: set[int] = set()
        for channel in channels:
            seen_channel_ids.add(channel.id)
            if await onboarding_queue.enqueue(channel.id, message.from_user.id):
                queued += 1
        for job in failed_jobs:
            if job.channel_id in seen_channel_ids:
                continue
            if await onboarding_queue.enqueue(job.channel_id, message.from_user.id):
                queued += 1

        total_checked = len(channels) + len([job for job in failed_jobs if job.channel_id not in seen_channel_ids])
        if total_checked == 0:
            await message.answer("Каналов без импортированных постов и упавших задач не нашлось.")
            return
        await message.answer(
            f"Проверил каналы без постов/с ошибками: {total_checked}.\n"
            f"Поставлено в очередь на обработку: {queued}."
        )

    @router.message(Command("queue"))
    async def queue_command(message: Message) -> None:
        async with session_scope() as session:
            repo = ChannelOnboardingJobRepository(session)
            counts = await repo.status_counts()
            jobs = await repo.recent_jobs(limit=8)

        lines = [
            "Очередь обработки каналов:",
            f"• pending: {counts.get('pending', 0)}",
            f"• processing: {counts.get('processing', 0)}",
            f"• failed: {counts.get('failed', 0)}",
            f"• completed: {counts.get('completed', 0)}",
            f"• в памяти worker-а: {len(onboarding_queue.pending_channel_ids)}",
        ]
        if jobs:
            lines.append("")
            lines.append("Последние задачи:")
            for job in jobs:
                title = job.channel.title if job.channel else f"channel_id={job.channel_id}"
                error = f" | ошибка: {job.last_error[:120]}" if job.last_error else ""
                lines.append(f"• {title}: {job.status}, попыток {job.attempts}{error}")

        await message.answer("\n".join(lines))

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
                reply_markup=feedback_keyboard(post),
                disable_web_page_preview=True,
            )

    @router.message(F.forward_from_chat)
    async def add_forwarded_channel(message: Message) -> None:
        if not message.from_user:
            return
        async with session_scope() as session:
            repo = ChannelRepository(session)
            try:
                channel = await channel_service.add_from_forward(message, repo)
            except ValueError as exc:
                await message.answer(str(exc))
                return
        enqueued = await onboarding_queue.enqueue(channel.id, message.from_user.id)
        if enqueued:
            await message.answer(
                f"Канал '{channel.title}' добавлен в отслеживание и поставлен в очередь на обработку."
            )
        else:
            await message.answer(f"Канал '{channel.title}' уже стоит в очереди на обработку.")

    @router.message(F.text.regexp(r"(https?://t\.me/|@)"))
    async def add_channel_by_text(message: Message) -> None:
        if not message.from_user:
            return
        async with session_scope() as session:
            repo = ChannelRepository(session)
            try:
                channel = await channel_service.add_from_text(message.text or "", repo)
            except Exception as exc:
                await message.answer(f"Не удалось добавить канал: {exc}")
                return
        enqueued = await onboarding_queue.enqueue(channel.id, message.from_user.id)
        if enqueued:
            await message.answer(
                f"Канал '{channel.title}' добавлен в отслеживание и поставлен в очередь на обработку."
            )
        else:
            await message.answer(f"Канал '{channel.title}' уже стоит в очереди на обработку.")

    @router.callback_query(F.data.startswith("feedback:"))
    async def feedback_callback(callback: CallbackQuery) -> None:
        parts = (callback.data or "").split(":")
        if not callback.from_user:
            await safe_callback_answer(callback, "Не удалось определить пользователя.", show_alert=True)
            return

        async with session_scope() as session:
            user = await UserRepository(session).get_or_create(
                callback.from_user.id, callback.from_user.username
            )
            repo = PostRepository(session)
            post = None

            if len(parts) == 4:
                _, chat_id_str, message_id_str, value_str = parts
                feedback_value = FeedbackValue(value_str)
                post = await repo.get_by_telegram_source(int(chat_id_str), int(message_id_str))
            elif len(parts) == 3:
                _, post_id_str, value_str = parts
                feedback_value = FeedbackValue(value_str)
                post = await repo.get(int(post_id_str))
            else:
                await safe_callback_answer(callback, "Некорректная кнопка.", show_alert=True)
                return

            if not post:
                await safe_callback_answer(
                    callback,
                    "Пост не найден в текущей базе. Пришлите новый дайджест.",
                    show_alert=True,
                )
                return
            await FeedbackRepository(session).add_feedback(user.id, post.id, feedback_value)
            if feedback_value == FeedbackValue.not_interested:
                post.relevance_score = max(0.0, post.relevance_score - 0.25)
            else:
                post.relevance_score = min(1.0, post.relevance_score + 0.15)
            await safe_callback_answer(callback, "Оценка сохранена.")
            if callback.message:
                await callback.message.edit_reply_markup(reply_markup=None)

    return router


async def _update_category_filter(
    message: Message,
    command: CommandObject,
    is_enabled: bool,
) -> None:
    if not message.from_user:
        return

    category = (command.args or "").strip()
    if not category:
        await message.answer(f"Укажите категорию: /category_{'on' if is_enabled else 'off'} <категория>")
        return

    async with session_scope() as session:
        user = await UserRepository(session).get_or_create(
            message.from_user.id, message.from_user.username
        )
        category_repo = UserCategoryPreferenceRepository(session)
        known_categories = await category_repo.known_categories()
        matched = _match_category(category, known_categories)
        if not matched:
            await message.answer(
                "Категория не найдена. Посмотрите доступные через /categories."
            )
            return
        await category_repo.set_enabled(user.id, matched, is_enabled=is_enabled)

    if is_enabled:
        await message.answer(f"Категория включена в дайджест: {matched}")
    else:
        await message.answer(f"Категория исключена из дайджеста: {matched}")


def _match_category(raw_value: str, known_categories: list[str]) -> str | None:
    normalized = raw_value.strip().casefold()
    for category in known_categories:
        if category.casefold() == normalized:
            return category
    for category in known_categories:
        if normalized in category.casefold():
            return category
    return None
