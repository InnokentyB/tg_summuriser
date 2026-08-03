from aiogram.types import BotCommand


BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Запустить бота"),
    BotCommand(command="help", description="Показать помощь"),
    BotCommand(command="add", description="Добавить канал"),
    BotCommand(command="channels", description="Список каналов"),
    BotCommand(command="categories", description="Настройки категорий"),
    BotCommand(command="digest", description="Собрать дайджест сейчас"),
    BotCommand(command="hidden", description="Показать скрытые посты"),
    BotCommand(command="search", description="Поиск по истории"),
]


HELP_TEXT = (
    "/add - инструкция по добавлению канала\n"
    "/channels - список каналов\n"
    "/categories - показать категории и текущий фильтр\n"
    "/category_on <категория> - включить категорию в дайджест\n"
    "/category_off <категория> - исключить категорию из дайджеста\n"
    "/category_reset - сбросить фильтр категорий\n"
    "/digest - собрать дайджест сейчас\n"
    "/hidden - показать скрытые посты\n"
    "/search <запрос> - поиск по истории"
)
