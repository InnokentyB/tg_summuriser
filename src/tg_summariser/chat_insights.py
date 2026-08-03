from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from tg_summariser.config import settings


@dataclass(slots=True)
class ChatMessage:
    message_id: int
    date: str
    sender_id: int | None
    text: str


@dataclass(slots=True)
class ChatInsightResult:
    raw_path: Path
    chunk_path: Path
    report_path: Path
    message_count: int


class ChatHistoryExporter:
    async def export_messages(self, chat_ref: str, limit: int | None) -> list[ChatMessage]:
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required.")

        from telethon import TelegramClient
        from telethon.sessions import StringSession

        session: str | Any = settings.telegram_session_name
        if settings.telegram_session_string:
            session = StringSession(settings.telegram_session_string)

        client = TelegramClient(session, settings.telegram_api_id, settings.telegram_api_hash)
        await client.connect()
        try:
            entity = await client.get_entity(chat_ref)
            messages: list[ChatMessage] = []
            async for message in client.iter_messages(entity, limit=limit, reverse=True):
                text = (message.message or "").strip()
                if not text:
                    continue
                messages.append(
                    ChatMessage(
                        message_id=message.id,
                        date=message.date.isoformat() if message.date else "",
                        sender_id=getattr(message, "sender_id", None),
                        text=text,
                    )
                )
            return messages
        finally:
            await client.disconnect()


class ChatInsightAnalyzer:
    def __init__(self, chunk_chars: int = 24_000) -> None:
        self.chunk_chars = chunk_chars
        self.client = AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    async def analyze(self, messages: list[ChatMessage], output_dir: Path, chat_ref: str) -> ChatInsightResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._safe_filename(chat_ref)
        raw_path = output_dir / f"{safe_name}.messages.jsonl"
        chunk_path = output_dir / f"{safe_name}.chunks.md"
        report_path = output_dir / f"{safe_name}.insights.md"

        self._write_jsonl(raw_path, messages)
        chunks = self._chunk_messages(messages)

        if not self.client:
            chunk_path.write_text(
                "OPENAI_API_KEY is not set. Raw chat history was exported, but AI analysis was skipped.\n",
                encoding="utf-8",
            )
            report_path.write_text(
                "# Chat Insights\n\nOPENAI_API_KEY is not set. Set it and rerun the command.\n",
                encoding="utf-8",
            )
            return ChatInsightResult(raw_path, chunk_path, report_path, len(messages))

        chunk_summaries: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_summaries.append(await self._analyze_chunk(index, len(chunks), chunk))

        chunk_path.write_text("\n\n".join(chunk_summaries), encoding="utf-8")
        final_report = await self._synthesize_report(chat_ref, chunk_summaries)
        report_path.write_text(final_report, encoding="utf-8")
        return ChatInsightResult(raw_path, chunk_path, report_path, len(messages))

    def _chunk_messages(self, messages: list[ChatMessage]) -> list[list[ChatMessage]]:
        chunks: list[list[ChatMessage]] = []
        current: list[ChatMessage] = []
        current_size = 0

        for message in messages:
            rendered = self._render_message(message)
            if current and current_size + len(rendered) > self.chunk_chars:
                chunks.append(current)
                current = []
                current_size = 0
            current.append(message)
            current_size += len(rendered)

        if current:
            chunks.append(current)
        return chunks

    async def _analyze_chunk(
        self,
        chunk_index: int,
        chunk_count: int,
        messages: list[ChatMessage],
    ) -> str:
        transcript = "\n".join(self._render_message(message) for message in messages)
        prompt = (
            "You analyze a Telegram group chat transcript for useful business/product insights.\n"
            "Return concise Markdown in Russian with sections:\n"
            "1. Key insights\n"
            "2. Repeated pain points\n"
            "3. Signals, opportunities, or decisions\n"
            "4. Notable quotes with message ids\n"
            "Avoid generic advice. Ground every insight in chat evidence.\n\n"
            f"Chunk {chunk_index}/{chunk_count} transcript:\n{transcript}"
        )
        response = await self.client.responses.create(model=settings.openai_model, input=prompt)
        return f"## Chunk {chunk_index}/{chunk_count}\n\n{self._extract_text(response)}"

    async def _synthesize_report(self, chat_ref: str, chunk_summaries: list[str]) -> str:
        prompt = (
            "You synthesize Telegram chat chunk analyses into one actionable report.\n"
            "Return Markdown in Russian with sections:\n"
            "- Executive summary\n"
            "- Top insights\n"
            "- Opportunities\n"
            "- Risks or recurring complaints\n"
            "- Suggested next actions\n"
            "- Evidence map with message ids where available\n"
            "Keep it dense and practical.\n\n"
            f"Chat: {chat_ref}\n\nChunk analyses:\n" + "\n\n".join(chunk_summaries)
        )
        response = await self.client.responses.create(model=settings.openai_model, input=prompt)
        return self._extract_text(response)

    @staticmethod
    def _render_message(message: ChatMessage) -> str:
        return f"[{message.message_id}] {message.date} sender={message.sender_id}: {message.text}"

    @staticmethod
    def _write_jsonl(path: Path, messages: list[ChatMessage]) -> None:
        with path.open("w", encoding="utf-8") as file:
            for message in messages:
                file.write(json.dumps(asdict(message), ensure_ascii=False) + "\n")

    @staticmethod
    def _safe_filename(value: str) -> str:
        safe = "".join(ch if ch.isalnum() else "_" for ch in value).strip("_")
        return safe[:80] or f"chat_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

    @staticmethod
    def _extract_text(response: Any) -> str:
        if hasattr(response, "output_text") and response.output_text:
            return response.output_text
        for item in getattr(response, "output", []):
            for content in getattr(item, "content", []):
                if getattr(content, "type", "") == "output_text":
                    return content.text
        return ""


async def run(args: argparse.Namespace) -> ChatInsightResult:
    limit = None if args.limit == 0 else args.limit
    messages = await ChatHistoryExporter().export_messages(args.chat_ref, limit=limit)
    analyzer = ChatInsightAnalyzer(chunk_chars=args.chunk_chars)
    return await analyzer.analyze(messages, output_dir=Path(args.output_dir), chat_ref=args.chat_ref)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export a Telegram chat history and extract insights.")
    parser.add_argument("chat_ref", help="Telegram chat reference: @username, t.me link, invite entity, or id.")
    parser.add_argument("--limit", type=int, default=5000, help="Messages to scan. Use 0 for all available history.")
    parser.add_argument("--chunk-chars", type=int, default=24_000, help="Approximate characters per AI chunk.")
    parser.add_argument("--output-dir", default="exports/chat_insights", help="Directory for JSONL and reports.")
    return parser


def main() -> None:
    result = asyncio.run(run(build_parser().parse_args()))
    print(f"Exported messages: {result.message_count}")
    print(f"Raw JSONL: {result.raw_path}")
    print(f"Chunk notes: {result.chunk_path}")
    print(f"Report: {result.report_path}")


if __name__ == "__main__":
    main()
