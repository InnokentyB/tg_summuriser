from pathlib import Path

from tg_summariser.chat_insights import ChatInsightAnalyzer, ChatMessage


def test_chunk_messages_respects_chunk_size() -> None:
    analyzer = ChatInsightAnalyzer(chunk_chars=120)
    messages = [
        ChatMessage(message_id=1, date="2026-01-01T00:00:00", sender_id=10, text="A" * 80),
        ChatMessage(message_id=2, date="2026-01-01T00:01:00", sender_id=11, text="B" * 80),
    ]

    chunks = analyzer._chunk_messages(messages)

    assert len(chunks) == 2
    assert chunks[0][0].message_id == 1
    assert chunks[1][0].message_id == 2


def test_safe_filename_removes_unsafe_characters() -> None:
    assert ChatInsightAnalyzer._safe_filename("https://t.me/some-chat") == "https___t_me_some_chat"


async def test_analyze_without_openai_writes_raw_export(tmp_path: Path) -> None:
    analyzer = ChatInsightAnalyzer()
    analyzer.client = None
    messages = [ChatMessage(message_id=1, date="2026-01-01T00:00:00", sender_id=10, text="hello")]

    result = await analyzer.analyze(messages, output_dir=tmp_path, chat_ref="@test_chat")

    assert result.message_count == 1
    assert result.raw_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY is not set" in result.report_path.read_text(encoding="utf-8")
