from __future__ import annotations

import asyncio
import getpass

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from tg_summariser.config import settings


async def main() -> None:
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required.")

    phone = input("Telegram phone (international format): ").strip()
    if not phone:
        raise RuntimeError("Phone is required.")

    client = TelegramClient(StringSession(), settings.telegram_api_id, settings.telegram_api_hash)
    await client.connect()
    try:
        await client.send_code_request(phone)
        code = input("Login code: ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            password = getpass.getpass("2FA password: ")
            await client.sign_in(password=password)

        session_string = client.session.save()
        me = await client.get_me()
        print("\nAuthenticated successfully.")
        print(f"Telegram user: {getattr(me, 'username', None) or me.id}")
        print("\nTELEGRAM_SESSION_STRING:")
        print(session_string)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
