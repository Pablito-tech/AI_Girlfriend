"""Prueba de punta a punta con la librería real de Telegram y un servidor de Telegram falso."""

import asyncio
import json

import pytest

pytest.importorskip("telegram")

from telegram import Update  # noqa: E402
from telegram.request import BaseRequest  # noqa: E402

from ali.telegram_bot import build_application  # noqa: E402
from tests.fakes import FakeClient, response, text  # noqa: E402

OWNER, STRANGER = 42, 7


class FakeTelegramAPI(BaseRequest):
    """Responde como la API de bots de Telegram y guarda lo que el bot envía."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    @property
    def read_timeout(self):
        return 1.0

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((endpoint, params))
        if endpoint == "getMe":
            result = {"id": 1, "is_bot": True, "first_name": "Ali", "username": "ali_bot"}
        elif endpoint == "sendMessage":
            result = {"message_id": len(self.calls), "date": 0,
                      "chat": {"id": params["chat_id"], "type": "private"}, "text": params["text"]}
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()

    def sent(self) -> list[str]:
        return [p["text"] for e, p in self.calls if e == "sendMessage"]


def message(update_id: int, user_id: int, body: str) -> dict:
    msg = {"message_id": update_id, "date": 0, "chat": {"id": user_id, "type": "private"},
           "from": {"id": user_id, "is_bot": False, "first_name": "X"}, "text": body}
    if body.startswith("/"):
        msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(body.split()[0])}]
    return {"update_id": update_id, "message": msg}


def test_bot_talks_only_to_its_owner(config, clock):
    config.telegram_token = "123:ABC"
    config.telegram_user_id = OWNER
    config.telegram_wait_s = 0.05
    config.web_search = False

    async def scenario():
        api = FakeTelegramAPI()
        client = FakeClient([response(text("¡Hola! Qué gusto leerte ♡"))])
        app, bot = build_application(config, client=client, request=api)
        async with app:
            await app.post_init(app)
            for update in (message(1, STRANGER, "hola, soy un desconocido"),
                           message(2, OWNER, "hola Ali"),
                           message(3, OWNER, "/agenda")):
                await app.process_update(Update.de_json(update, app.bot))
            await bot.wait_idle()
            await app.post_stop(app)

        assert len(client.messages.calls) == 1  # el desconocido no gastó tu API
        assert "¡Hola! Qué gusto leerte ♡" in api.sent()
        assert any("No hay nada" in t for t in api.sent())
        assert all(p["chat_id"] == OWNER for e, p in api.calls if e == "sendMessage")
        assert any(e == "setMyCommands" for e, _ in api.calls)

    asyncio.run(scenario())
