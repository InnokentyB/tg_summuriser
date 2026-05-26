from aiogram.types import BotCommand


BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Запустить бота"),
    BotCommand(command="help", description="Показать помощь"),
    BotCommand(command="add", description="Добавить канал"),
    BotCommand(command="channels", description="Список каналов"),
    BotCommand(command="digest", description="Собрать дайджест сейчас"),
    BotCommand(command="hidden", description="Показать скрытые посты"),
    BotCommand(command="search", description="Поиск по истории"),
]


HELP_TEXT = (
    "/add - инструкция по добавлению канала\n"
    "/channels - список каналов\n"
    "/digest - собрать дайджест сейчас\n"
    "/hidden - показать скрытые посты\n"
    "/search <запрос> - поиск по истории"
)

