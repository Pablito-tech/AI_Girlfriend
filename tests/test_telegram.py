import asyncio
import time
from pathlib import Path

import pytest

from ali.telegram_bot import AliTelegram, reply_text, split_bubbles
from ali.brain import Event
from tests.fakes import FakeClient, response, text, tool_use
from tests.test_core import sample_reflection


class FakeSender:
    def __init__(self):
        self.sent: list[str] = []
        self.documents: list[Path] = []

    async def text(self, text: str, monospace: bool = False) -> None:
        self.sent.append(text)

    async def typing(self) -> None:
        pass

    async def document(self, path: Path, caption: str = "") -> None:
        self.documents.append(path)


@pytest.fixture
def tg_config(config):
    config.telegram_user_id = 42
    config.telegram_wait_s = 0.05
    config.web_search = False
    return config


def run(coro):
    return asyncio.run(coro)


def test_split_bubbles():
    assert split_bubbles("hola\n\n¿cómo estás?\n\n\n") == ["hola", "¿cómo estás?"]
    assert split_bubbles("a\n\nb\n\nc\n\nd\n\ne") == ["a", "b", "c", "d\n\ne"]
    long = ("palabra " * 1200).strip()
    bubbles = split_bubbles(long, limit=4000)
    assert all(len(b) <= 4000 for b in bubbles) and " ".join(bubbles) == long


def test_reply_text_separates_rounds_and_errors():
    events = [Event("round"), Event("text", "Déjame ver"), Event("tool", "ver_agenda"),
              Event("round"), Event("text", "¡Tienes dentista!"), Event("error", "ups")]
    body, notes = reply_text(events)
    assert split_bubbles(body) == ["Déjame ver", "¡Tienes dentista!"]
    assert notes == ["⚠ ups"]


def test_only_the_owner_is_authorized(tg_config, clock):
    async def scenario():
        bot = AliTelegram(tg_config, FakeSender(), client=FakeClient())
        assert bot.is_authorized(42) and not bot.is_authorized(7) and not bot.is_authorized(None)
        await bot.shutdown()
    run(scenario())


def test_messages_in_a_row_are_answered_together(tg_config, clock):
    async def scenario():
        client = FakeClient([response(text("¡Holaaa!\n\nYo bien, ¿y tú?"))])
        sender = FakeSender()
        bot = AliTelegram(tg_config, sender, client=client)
        await bot.receive("hola")
        await bot.receive("¿cómo estás?")
        await bot.wait_idle()
        assert len(client.messages.calls) == 1
        user_turn = client.messages.calls[0]["messages"][-1]["content"]
        assert user_turn[-1]["text"] == "hola\n¿cómo estás?"
        assert "Telegram" in user_turn[0]["text"]
        assert sender.sent == ["¡Holaaa!", "Yo bien, ¿y tú?"]
        await bot.shutdown()
    run(scenario())


def test_photos_reach_ali_as_images(tg_config, clock, tmp_path):
    async def scenario():
        img = tmp_path / "captura.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
        client = FakeClient([response(text("¡Qué escena tan bonita!"))])
        bot = AliTelegram(tg_config, FakeSender(), client=client)
        await bot.receive("", images=[img])
        await bot.wait_idle()
        blocks = client.messages.calls[0]["messages"][-1]["content"]
        assert [b["type"] for b in blocks] == ["text", "image", "text"]
        assert blocks[-1]["text"] == "Mira esto."
        await bot.shutdown()
    run(scenario())


def test_ali_reminds_upcoming_events_once(tg_config, clock):
    async def scenario():
        client = FakeClient([response(text("¡Oye! En 20 minutos tienes dentista 🦷"))])
        sender = FakeSender()
        bot = AliTelegram(tg_config, sender, client=client)
        await bot._in_ali(bot.ali.skills.execute, "agregar_evento",
                          {"titulo": "Dentista", "fecha_hora": "2026-09-27T18:50"})
        await bot._in_ali(bot.ali.skills.execute, "agregar_evento",
                          {"titulo": "Cumpleaños de mamá", "fecha_hora": "2026-09-28"})  # todo el día: sin alarma
        await bot.tick()
        await bot.tick()  # no se repite
        assert sender.sent == ["¡Oye! En 20 minutos tienes dentista 🦷"]
        note = client.messages.calls[0]["messages"][-1]["content"][-1]["text"]
        assert "Dentista" in note and "20 minutos" in note
        # el aviso no se guarda como si lo hubieras escrito tú
        stored = await bot._in_ali(bot.ali.db.recent_messages, 10)
        assert [m["role"] for m in stored] == ["assistant"]
        await bot.shutdown()
    run(scenario())


def test_idle_session_is_closed_and_consolidated(tg_config, clock):
    async def scenario():
        client = FakeClient([response(text("jaja"))], parsed=sample_reflection())
        bot = AliTelegram(tg_config, FakeSender(), client=client)
        await bot.receive("Toby se quedó viendo la pared jajaja")
        await bot.wait_idle()
        assert bot.ali.session_id is not None

        await bot.tick()
        assert bot.ali.session_id is not None  # aún no pasa suficiente tiempo

        bot.last_activity = time.monotonic() - 31 * 60
        await bot.tick()
        assert bot.ali.session_id is None
        assert len(client.messages.parse_calls) == 1
        assert bot.ali.personality.user["apodos"] == ["cielo"]
        await bot.shutdown()
    run(scenario())


def test_good_morning_is_sent_once_a_day_with_the_agenda(tg_config, clock):
    async def scenario():
        clock.t = clock.t.replace(hour=8, minute=30)
        client = FakeClient([response(text("¡Buenos días! ☀️ Hoy tienes junta a las 11."))])
        sender = FakeSender()
        bot = AliTelegram(tg_config, sender, client=client)
        await bot._in_ali(bot.ali.skills.execute, "agregar_evento",
                          {"titulo": "Junta", "fecha_hora": "2026-09-27T11:00"})
        await bot.good_morning()
        await bot.good_morning()
        assert len(sender.sent) == 1
        assert "Junta" in client.messages.calls[0]["messages"][-1]["content"][-1]["text"]
        await bot.shutdown()
    run(scenario())


def test_commands(tg_config, clock):
    async def scenario():
        sender = FakeSender()
        bot = AliTelegram(tg_config, sender, client=FakeClient())
        await bot.command("ali")
        await bot.command("agenda")
        await bot.command("exportar")
        await bot.command("inventado")
        assert sender.sent[0].startswith("Ali — confianza")
        assert "No hay nada" in sender.sent[1]
        assert "No conozco" in sender.sent[2]
        assert sender.documents and sender.documents[0].exists()
        await bot.shutdown()
    run(scenario())


def test_tool_use_through_telegram(tg_config, clock):
    async def scenario():
        client = FakeClient([
            response(text("Déjame anotarlo."),
                     tool_use("t1", "agregar_evento", {"titulo": "Cine", "fecha_hora": "2026-09-30T20:00"}),
                     stop_reason="tool_use"),
            response(text("¡Listo! El miércoles a las 8 🎬")),
        ])
        sender = FakeSender()
        bot = AliTelegram(tg_config, sender, client=client)
        await bot.receive("vamos al cine el miércoles a las 8pm")
        await bot.wait_idle()
        assert sender.sent == ["Déjame anotarlo.", "¡Listo! El miércoles a las 8 🎬"]
        await bot.shutdown()
    run(scenario())
