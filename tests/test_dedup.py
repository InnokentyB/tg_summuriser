from tg_summariser.models import Post
from tg_summariser.services.dedup import Deduplicator


def make_post(post_id: int, text: str) -> Post:
    return Post(
        id=post_id,
        channel_id=1,
        telegram_message_id=post_id,
        raw_text=text,
        normalized_text=text,
    )


def test_find_duplicate_returns_matching_post_id() -> None:
    dedup = Deduplicator()
    current = make_post(2, "OpenAI released a new model for agent workflows today")
    existing = [make_post(1, "OpenAI released a new model for agent workflows today")]

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id == 1


def test_find_duplicate_matches_rewritten_news_with_shared_facts() -> None:
    dedup = Deduplicator()
    current = make_post(
        2,
        "OpenAI представила GPT-5 mini для агентских сценариев и автоматизации "
        "рабочих процессов. Подробнее: https://t.me/ai_news/42",
    )
    existing = [
        make_post(
            1,
            "Компания OpenAI выпустила модель GPT-5 mini для AI-агентов и "
            "автоматизации workflows.",
        )
    ]

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id == 1


def test_find_duplicate_uses_ai_summary_when_original_text_is_rewritten() -> None:
    dedup = Deduplicator()
    current = make_post(2, "Короткий пост с другим текстом")
    current.summary = "OpenAI выпустила GPT-5 mini для AI-агентов и автоматизации."
    existing = [make_post(1, "Совсем другая формулировка новости")]
    existing[0].summary = "OpenAI представила GPT-5 mini для агентских сценариев и автоматизации."

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id == 1


def test_find_duplicate_matches_same_news_across_sources() -> None:
    dedup = Deduplicator()
    current = make_post(
        2,
        "OpenAI объявила, что её ещё не выпущенная модель решила десять открытых задач "
        "в математике. Автор считает это потенциально революционным и отмечает "
        "необходимость проверки заявлений.",
    )
    current.summary = (
        "OpenAI объявила, что невыпущенная модель решила десять открытых задач "
        "в математике и может изменить исследовательский процесс."
    )
    existing = [
        make_post(
            1,
            "OpenAI сообщает, что новая невыпущенная модель Astra решила десять "
            "математических задач, по которым не было прогресса десять лет; "
            "доказательства формализованы в Lean.",
        )
    ]
    existing[0].summary = (
        "OpenAI сообщает, что модель Astra решила десять математических задач "
        "и формализовала доказательства в Lean."
    )

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id == 1


def test_find_duplicate_matches_leveraged_ai_fund_news() -> None:
    dedup = Deduplicator()
    current = make_post(
        2,
        "Леопольд Ашенбреннер, бывший сотрудник OpenAI, создал сильно "
        "левереджированный фонд на инфраструктуру ИИ: чипы, память, облака "
        "и шорты на старый SaaS. При плече до 4x и маржин-коллах фонд резко просел.",
    )
    existing = [
        make_post(
            1,
            "Управляющий 22 года, бывший исследователь OpenAI, при плечах x4 "
            "лонговал AI-акции и производителей чипов и шортил традиционный SaaS; "
            "после плохих дней фонд потерял большую часть стоимости.",
        )
    ]

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id == 1


def test_find_duplicate_ignores_different_posts() -> None:
    dedup = Deduplicator()
    current = make_post(2, "A deep dive into business moats")
    existing = [make_post(1, "Prompt engineering techniques for tool use")]

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id is None


def test_find_duplicate_keeps_related_but_different_news_separate() -> None:
    dedup = Deduplicator()
    current = make_post(
        2,
        "OpenAI купила стартап для генерации видео и планирует интегрировать команду.",
    )
    existing = [
        make_post(
            1,
            "OpenAI выпустила GPT-5 mini для AI-агентов и автоматизации workflows.",
        )
    ]

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id is None


def test_find_duplicate_keeps_distinct_arxiv_papers_separate() -> None:
    dedup = Deduplicator()
    current = make_post(
        2,
        "Title: [2608.20653] Meta-clustering of milk spectra Source: arXiv cs.LG "
        "Abstract: This machine learning study evaluates clustering methods and model results.",
    )
    existing = [
        make_post(
            1,
            "Title: [2608.04060] SJEPA latent dynamics Source: arXiv cs.LG "
            "Abstract: This machine learning study evaluates predictive methods and model results.",
        )
    ]

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id is None


def test_find_duplicate_matches_same_arxiv_paper_version() -> None:
    dedup = Deduplicator()
    current = make_post(2, "arXiv:2608.20653v2 updated abstract for clustering research")
    existing = [make_post(1, "[2608.20653] original abstract for clustering research")]

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id == 1
