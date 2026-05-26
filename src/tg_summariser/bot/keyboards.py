from tg_summariser.models import Post
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def feedback_keyboard(post: Post) -> InlineKeyboardMarkup:
    if post.channel and post.channel.telegram_chat_id:
        callback_prefix = f"feedback:{post.channel.telegram_chat_id}:{post.telegram_message_id}"
    else:
        callback_prefix = f"feedback:{post.id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Интересно", callback_data=f"{callback_prefix}:interested"),
                InlineKeyboardButton(
                    text="Неинтересно", callback_data=f"{callback_prefix}:not_interested"
                ),
            ]
        ]
    )
