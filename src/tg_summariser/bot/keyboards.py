from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def feedback_keyboard(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Интересно", callback_data=f"feedback:{post_id}:interested"),
                InlineKeyboardButton(
                    text="Неинтересно", callback_data=f"feedback:{post_id}:not_interested"
                ),
            ]
        ]
    )

