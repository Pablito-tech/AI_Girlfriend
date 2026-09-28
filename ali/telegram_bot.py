"""Ali en Telegram.

Para usarlo: `python telegram_bot.py` (ver README, sección Telegram).

Cómo funciona:
- Sólo responde a TU cuenta (TELEGRAM_USER_ID). Si aún no lo configuraste, el
  bot arranca en "modo configuración" y te dice cuál es tu ID.
- Espera un par de segundos a que termines de escribir: si mandas varios
  mensajes seguidos, Ali los lee juntos y responde una vez (como una persona).
- Sus respuestas llegan en burbujas separadas, como en un chat real.
- Te escribe ella primero para recordarte eventos de la agenda y, si quieres,
  para darte los buenos días.
- Como en Telegram no hay "/salir", la conversación se da por terminada tras
  un rato sin mensajes; ahí Ali repasa lo que hablaron y consolida recuerdos.

La clase `AliTelegram` no depende de la librería de Telegram (así se puede
probar); `run()` la conecta con python-telegram-bot.
"""

from __future__ import annotations

import asyncio
import functools
import html
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import time as dtime
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

from . import commands
from .brain import Event
from .config import Config
from .core import GREETING_PROMPT, Ali
from .utils import IMAGE_TYPES, iso, now, parse_dt

log = logging.getLogger("ali.telegram")

MAX_BUBBLES = 4
TELEGRAM_LIMIT = 4000
TYPING_EVERY_S = 4.0
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # límite de descarga de la API de bots
UPLOAD_SUBDIR = "telegram"

HELP = """\
Háblame normal, como en cualquier chat ♡ También puedes mandarme fotos o archivos.

Comandos:
/ali — cómo soy hoy (rasgos, humor, ánimo)
/perfil — lo que sé de ti
/recuerdos [texto] — mis recuerdos recientes o buscar uno
/olvidar <id> — borrar un recuerdo
/agenda — tu agenda
/obras — tu lista de anime/series/libros
/reflexionar — consolidar recuerdos ahora
/exportar — respaldo de todo en un archivo
/ayuda — este mensaje"""

BOT_COMMANDS = [
    ("ali", "Cómo es Ali hoy"),
    ("perfil", "Lo que Ali sabe de ti"),
    ("recuerdos", "Ver o buscar recuerdos"),
    ("agenda", "Ver tu agenda"),
    ("obras", "Tu lista de anime/series/libros"),
    ("reflexionar", "Consolidar recuerdos ahora"),
    ("exportar", "Respaldo de todo"),
    ("ayuda", "Ayuda"),
]


def split_bubbles(text: str, max_bubbles: int = MAX_BUBBLES, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Divide la respuesta en burbujas de chat (por párrafos) respetando el límite de Telegram."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(parts) > max_bubbles:
        parts = parts[: max_bubbles - 1] + ["\n\n".join(parts[max_bubbles - 1:])]
    bubbles = []
    for part in parts:
        while len(part) > limit:
            cut = part.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = part.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            bubbles.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            bubbles.append(part)
    return bubbles


def reply_text(events: list[Event]) -> tuple[str, list[str]]:
    """Junta el texto de Ali y separa los avisos de error."""
    reply: list[str] = []
    notes: list[str] = []
    mark = 0
    for ev in events:
        if ev.kind == "round":
            if reply and not reply[-1].endswith("\n"):
                reply.append("\n\n")  # texto antes y después de usar una herramienta: burbujas distintas
            mark = len(reply)
        elif ev.kind == "retry":
            del reply[mark:]
        elif ev.kind == "text":
            reply.append(ev.text)
        elif ev.kind == "error":
            notes.append(f"⚠ {ev.text}")
        elif ev.kind == "refusal":
            notes.append(f"({ev.text})")
    return "".join(reply).strip(), notes


class Sender(Protocol):
    async def text(self, text: str, monospace: bool = False) -> None: ...
    async def typing(self) -> None: ...
    async def document(self, path: Path, caption: str = "") -> None: ...


class AliTelegram:
    """Lógica del bot: mensajes, comandos, sesiones y mensajes por iniciativa de Ali."""

    def __init__(self, config: Config, sender: Sender, client: Any | None = None):
        self.config = config
        self.sender = sender
        # Ali y su base de datos viven siempre en este único hilo; así nunca se
        # atienden dos cosas a la vez ni se bloquea la conexión con Telegram.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ali")
        self.ali: Ali = self._executor.submit(self._create_ali, client).result()
        self.pending: list[tuple[str, list[Path]]] = []
        self.last_activity = time.monotonic()
        self._lock = asyncio.Lock()
        self._timer: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    def _create_ali(self, client: Any | None) -> Ali:
        ali = Ali(self.config, client=client)
        ali.channel = "Telegram"
        ali.db.execute(
            """CREATE TABLE IF NOT EXISTS notifications (
                   kind TEXT NOT NULL, ref TEXT NOT NULL, sent_at TEXT NOT NULL,
                   PRIMARY KEY (kind, ref))"""
        )
        return ali

    async def _in_ali(self, fn: Callable, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, functools.partial(fn, *args, **kwargs))

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @property
    def uploads_dir(self) -> Path:
        path = self.config.files_dir / UPLOAD_SUBDIR
        path.mkdir(parents=True, exist_ok=True)
        return path

    def is_authorized(self, user_id: int | None) -> bool:
        return self.config.telegram_user_id is not None and user_id == self.config.telegram_user_id

    # ------------------------------------------------------------------
    # Mensajes tuyos
    # ------------------------------------------------------------------

    async def receive(self, text: str, images: list[Path] | None = None) -> None:
        """Llega un mensaje: espera un momento por si sigues escribiendo."""
        self.pending.append((text.strip(), list(images or [])))
        self.last_activity = time.monotonic()
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = asyncio.create_task(self._debounce())

    async def _debounce(self) -> None:
        await asyncio.sleep(self.config.telegram_wait_s)
        self._spawn(self._process_pending())  # ya no se puede cancelar: Ali empieza a responder

    async def _process_pending(self) -> None:
        async with self._lock:
            if not self.pending:
                return
            batch, self.pending = self.pending, []
            text = "\n".join(t for t, _ in batch if t) or "Mira esto."
            images = [p for _, imgs in batch for p in imgs]
            await self._respond(self._chat, text, images)

    async def wait_idle(self) -> None:
        """Espera a que Ali termine todo lo pendiente (útil al apagar y en pruebas)."""
        while True:
            tasks = [t for t in [self._timer, *self._tasks] if t and not t.done()]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- trabajo en el hilo de Ali -----------------------------------

    def _ensure_session(self) -> None:
        if self.ali.session_id is None:
            self.ali.consolidate_pending()
            self.ali.start_session()

    def _chat(self, text: str, images: list[Path]) -> list[Event]:
        self._ensure_session()
        return list(self.ali.chat(text, images=images))

    def _proactive(self, instruction: str) -> list[Event]:
        self._ensure_session()
        return list(self.ali.proactive(instruction))

    # --- entregar la respuesta ---------------------------------------

    async def _keep_typing(self) -> None:
        while True:
            try:
                await self.sender.typing()
            except Exception:  # noqa: BLE001 - el indicador de "escribiendo" no es crítico
                pass
            await asyncio.sleep(TYPING_EVERY_S)

    async def _respond(self, fn: Callable[..., list[Event]], *args) -> None:
        typing = asyncio.create_task(self._keep_typing())
        try:
            events = await self._in_ali(fn, *args)
        except Exception as e:  # noqa: BLE001 - un error no debe apagar el bot
            log.exception("Error al responder")
            events = [Event("error", f"Algo falló: {type(e).__name__}: {e}")]
        finally:
            typing.cancel()
        text, notes = reply_text(events)
        bubbles = split_bubbles(text)
        for i, bubble in enumerate(bubbles):
            await self.sender.text(bubble)
            if i < len(bubbles) - 1:  # pausita entre burbujas, como alguien que escribe
                await self.sender.typing()
                await asyncio.sleep(min(1.5, 0.3 + len(bubbles[i + 1]) / 200))
        for note in notes:
            await self.sender.text(note)
        self.last_activity = time.monotonic()

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------

    async def command(self, name: str, arg: str = "") -> None:
        name = name.lower()
        simple: dict[str, Callable[[Ali], str]] = {
            "perfil": commands.profile,
            "recuerdos": lambda a: commands.memories(a, arg),
            "olvidar": lambda a: commands.forget(a, arg),
            "agenda": lambda a: commands.run_tool(a, "ver_agenda"),
            "obras": lambda a: commands.run_tool(a, "ver_obras"),
            "reflexionar": commands.reflect,
        }
        if name == "start":
            async with self._lock:
                await self._respond(self._proactive, GREETING_PROMPT)
        elif name in ("ayuda", "help"):
            await self.sender.text(HELP)
        elif name == "ali":
            await self.sender.text(await self._in_ali(commands.personality, self.ali), monospace=True)
        elif name in simple:
            await self.sender.text(await self._in_ali(simple[name], self.ali))
        elif name == "exportar":
            path = await self._in_ali(self.ali.export)
            await self.sender.document(path, "Respaldo completo de Ali ♡")
        else:
            await self.sender.text("No conozco ese comando. Escribe /ayuda.")

    # ------------------------------------------------------------------
    # Cosas que pasan solas (se llama cada minuto)
    # ------------------------------------------------------------------

    async def tick(self) -> None:
        await self._close_idle_session()
        await self._send_reminders()

    async def _close_idle_session(self) -> None:
        idle = time.monotonic() - self.last_activity
        if (
            self.ali.session_id is not None
            and not self.pending
            and not self._lock.locked()
            and idle > self.config.telegram_session_min * 60
        ):
            async with self._lock:
                await self._in_ali(self.ali.end_session)  # aquí Ali repasa y consolida recuerdos

    def _mark_sent(self, kind: str, ref: str) -> bool:
        """Registra un aviso. Devuelve False si ya se había enviado (no se repite)."""
        cur = self.ali.db.execute(
            "INSERT OR IGNORE INTO notifications (kind, ref, sent_at) VALUES (?, ?, ?)", (kind, ref, iso(now()))
        )
        return cur.rowcount == 1

    def _due_reminders(self) -> list[dict]:
        if self.config.telegram_reminder_min <= 0 or not self.ali.skills.get("agregar_evento"):
            return []
        at = now()
        rows = self.ali.db.query(
            "SELECT * FROM events WHERE done = 0 AND starts_at > ? AND starts_at <= ? ORDER BY starts_at",
            (iso(at), iso(at + timedelta(minutes=self.config.telegram_reminder_min))),
        )
        due = []
        for r in rows:
            if parse_dt(r["starts_at"]).time() == dtime(0, 0):
                continue  # evento de todo el día: se menciona en los buenos días, no con alarma
            if self._mark_sent("recordatorio", f"{r['id']}@{r['starts_at']}"):
                due.append(dict(r))
        return due

    async def _send_reminders(self) -> None:
        for ev in await self._in_ali(self._due_reminders):
            start = parse_dt(ev["starts_at"])
            minutes = max(1, round((start - now()).total_seconds() / 60))
            notes = f" (notas: {ev['notes']})" if ev["notes"] else ""
            instruction = (
                f"En {minutes} minutos, a las {start:%H:%M}, tu pareja tiene en su agenda: «{ev['title']}»{notes}. "
                "Escríbele para recordárselo, breve y con tu estilo."
            )
            async with self._lock:
                await self._respond(self._proactive, instruction)

    def _today_agenda(self) -> str:
        if not self.ali.skills.get("ver_agenda"):
            return ""
        today = f"{now():%Y-%m-%d}"
        out = commands.run_tool(self.ali, "ver_agenda", {"desde": today, "hasta": today})
        return "" if out.startswith("No hay nada") else out

    async def good_morning(self) -> None:
        if not await self._in_ali(self._mark_sent, "buenos_dias", f"{now():%Y-%m-%d}"):
            return
        agenda = await self._in_ali(self._today_agenda)
        instruction = "Es la mañana. Mándale a tu pareja un mensaje corto de buenos días, con tu estilo."
        if agenda:
            instruction += f" Su agenda de hoy es:\n{agenda}\nMenciónala con naturalidad."
        async with self._lock:
            await self._respond(self._proactive, instruction)

    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
            self._spawn(self._process_pending())  # responde lo que quedó en espera
        await self.wait_idle()
        async with self._lock:
            await self._in_ali(self.ali.end_session)
            await self._in_ali(self.ali.close)
        self._executor.shutdown(wait=True)


# ----------------------------------------------------------------------
# Conexión con Telegram (python-telegram-bot)
# ----------------------------------------------------------------------


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._") or "archivo"
    return f"{now():%Y%m%d_%H%M%S}_{name[-80:]}"


def _parse_hhmm(value: str) -> dtime | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return dtime(int(m.group(1)), int(m.group(2)), tzinfo=datetime.now().astimezone().tzinfo)


def build_application(config: Config, client: Any | None = None, request: Any | None = None):
    """Crea la aplicación de Telegram con todos sus manejadores. Devuelve (app, bot)."""
    from telegram import BotCommand, Update
    from telegram.constants import ChatAction, ParseMode
    from telegram.ext import Application, ContextTypes, MessageHandler, filters

    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands([BotCommand(c, d) for c, d in BOT_COMMANDS])

    async def post_stop(application: Application) -> None:
        # Todavía se pueden mandar mensajes: se responde lo pendiente y se consolidan recuerdos.
        print(f"({bot.ali.name} está guardando sus recuerdos…)")
        await bot.shutdown()

    builder = Application.builder().token(config.telegram_token)
    if request is not None:
        builder = builder.request(request).get_updates_request(request)
    app = builder.post_init(post_init).post_stop(post_stop).build()
    chat_id = config.telegram_user_id  # en un chat privado, el chat y el usuario tienen el mismo ID

    class TelegramSender:
        async def text(self, text: str, monospace: bool = False) -> None:
            if monospace:
                await app.bot.send_message(chat_id, f"<pre>{html.escape(text)}</pre>", parse_mode=ParseMode.HTML)
            else:
                await app.bot.send_message(chat_id, text)

        async def typing(self) -> None:
            await app.bot.send_chat_action(chat_id, ChatAction.TYPING)

        async def document(self, path: Path, caption: str = "") -> None:
            with open(path, "rb") as f:
                await app.bot.send_document(chat_id, document=f, filename=path.name, caption=caption)

    bot = AliTelegram(config, TelegramSender(), client=client)
    me = filters.User(user_id=config.telegram_user_id)

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await bot.receive(update.effective_message.text or "")

    async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        parts = (update.effective_message.text or "").split(maxsplit=1)
        name = parts[0][1:].split("@")[0]
        await bot.command(name, parts[1] if len(parts) > 1 else "")

    async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        path = bot.uploads_dir / _safe_name(f"foto_{uuid.uuid4().hex[:6]}.jpg")
        await (await msg.photo[-1].get_file()).download_to_drive(path)
        await bot.receive(msg.caption or "", images=[path])

    async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg, doc = update.effective_message, update.effective_message.document
        if doc.file_size and doc.file_size > MAX_DOWNLOAD_BYTES:
            await msg.reply_text("Ese archivo es muy grande para mí (máximo 20 MB) 🥲")
            return
        path = bot.uploads_dir / _safe_name(doc.file_name or "archivo")
        await (await doc.get_file()).download_to_drive(path)
        if path.suffix.lower() in IMAGE_TYPES:
            await bot.receive(msg.caption or "", images=[path])
        else:
            rel = f"{UPLOAD_SUBDIR}/{path.name}"
            await bot.receive(f"{msg.caption or ''}\n[Te compartí el archivo «{rel}»; está en tu carpeta de archivos]")

    async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            "Todavía no puedo escuchar notas de voz 🙈 ¡pronto! Mientras, escríbeme ♡"
        )

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("Error en Telegram: %s", context.error)

    async def tick_job(context: ContextTypes.DEFAULT_TYPE) -> None:
        await bot.tick()

    async def morning_job(context: ContextTypes.DEFAULT_TYPE) -> None:
        await bot.good_morning()

    app.add_handler(MessageHandler(filters.COMMAND & me, on_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & me, on_text))
    app.add_handler(MessageHandler(filters.PHOTO & me, on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL & me, on_document))
    app.add_handler(MessageHandler((filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE) & me, on_voice))
    app.add_error_handler(on_error)  # los mensajes de otras personas se ignoran

    app.job_queue.run_repeating(tick_job, interval=60, first=15)
    if config.telegram_morning:
        at = _parse_hhmm(config.telegram_morning)
        if at:
            app.job_queue.run_daily(morning_job, time=at)
        else:
            print(f"⚠ ALI_TELEGRAM_BUENOS_DIAS='{config.telegram_morning}' no es una hora válida (usa HH:MM).")

    return app, bot


def run(config: Config | None = None) -> None:
    try:
        from telegram import Update
        from telegram.ext import Application, ContextTypes, MessageHandler, filters
    except ImportError:
        print("Falta la librería de Telegram. Ejecuta:  pip install -r requirements.txt")
        return

    logging.basicConfig(format="%(asctime)s %(name)s: %(message)s", level=logging.WARNING)
    config = config or Config.from_env()
    if not config.telegram_token:
        print("⚠ Falta TELEGRAM_BOT_TOKEN en tu archivo .env. Créalo hablando con @BotFather (ver README).")
        return
    problem = config.api_key_problem()
    if problem and config.telegram_user_id:  # en modo configuración todavía no hace falta
        print(f"⚠ {problem}")
        return

    builder = Application.builder().token(config.telegram_token)

    # --- modo configuración: todavía no sabemos quién eres -------------
    if not config.telegram_user_id:
        app = builder.build()

        async def tell_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            uid = update.effective_user.id if update.effective_user else "?"
            await update.effective_message.reply_text(
                f"¡Hola! Tu ID de Telegram es: {uid}\n\n"
                f"Ponlo en tu archivo .env así:\nTELEGRAM_USER_ID={uid}\n\n"
                "Luego apaga el bot (Ctrl+C) y vuelve a encenderlo."
            )

        app.add_handler(MessageHandler(filters.ALL, tell_id))
        print("Modo configuración: abre tu bot en Telegram y escríbele cualquier cosa para saber tu ID.")
        print("(Ctrl+C para apagar)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
        return

    app, bot = build_application(config)
    print(f"♡ {bot.ali.name} está en línea en Telegram. Deja esta ventana abierta; Ctrl+C para apagarla.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
