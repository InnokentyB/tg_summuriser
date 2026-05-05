from tg_summariser.bot.handlers import parse_search_args


def test_parse_search_args_with_filters() -> None:
    query, category, channel = parse_search_args(
        "ai agents; category=AI & Agents; channel=Latent Space"
    )
    assert query == "ai agents"
    assert category == "AI & Agents"
    assert channel == "Latent Space"


def test_parse_search_args_without_filters() -> None:
    query, category, channel = parse_search_args("business models")
    assert query == "business models"
    assert category is None
    assert channel is None

