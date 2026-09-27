"""Ali: une la personalidad, la memoria, las habilidades y a Claude.

Uso mínimo (cualquier interfaz — terminal, bot de Discord, web, voz — puede
construirse encima de esto):

    ali = Ali(Config.from_env())
    for event in ali.chat("¡Hola!"):
        if event.kind == "text":
            print(event.text, end="")
    ali.end_session()   # consolida recuerdos y guarda todo
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator

import anthropic

from .brain import ClaudeBrain, Event
from .config import Config
from .memory import MemoryStore
from .personality import Personality
from .prompts import build_system_prompt
from .reflection import Reflexion, reflect_on_session
from .skills import SkillContext, SkillSet
from .storage import Database
from .utils import fecha_larga, image_block, iso, momento_del_dia, now

GREETING_PROMPT = (
    "(Tu pareja acaba de abrir el chat; todavía no ha escrito nada. Salúdale tú primero con naturalidad, "
    "según la hora, el tiempo que ha pasado desde la última vez y lo que sabes. Si hay algo pendiente o "
    "importante de lo que preguntar, puedes hacerlo. Sé breve.)"
)


def friendly_error(e: Exception, model: str) -> str:
    if isinstance(e, anthropic.AuthenticationError):
        return "Tu API key no es válida. Revisa ANTHROPIC_API_KEY en el archivo .env."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "Tu API key no tiene permiso para esta operación o este modelo."
    if isinstance(e, anthropic.NotFoundError):
        return f"El modelo '{model}' no existe o no está disponible en tu cuenta. Cambia ALI_MODEL en .env."
    if isinstance(e, anthropic.RateLimitError):
        return "Llegaste al límite de uso de la API por ahora. Espera un momento y vuelve a intentar."
    if isinstance(e, anthropic.BadRequestError):
        return f"La API rechazó la petición: {e.message}"
    if isinstance(e, anthropic.APIStatusError):
        return f"Error de la API ({e.status_code}). Intenta de nuevo en un momento."
    if isinstance(e, anthropic.APIConnectionError):
        return "No hay conexión con la API de Anthropic. Revisa tu internet."
    if "auth" in str(e).lower() or "api_key" in str(e).lower():
        return "No encontré tu API key. Ponla en el archivo .env como ANTHROPIC_API_KEY=..."
    return f"Error inesperado: {type(e).__name__}: {e}"


def _is_tool_result(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


class Ali:
    def __init__(self, config: Config, client: Any | None = None):
        config.ensure_dirs()
        self.config = config
        self.db = Database(config.db_path)
        self.memory = MemoryStore(self.db)
        self.personality = Personality.load(config.persona_file, config.personality_path)
        self.brain = ClaudeBrain(config, client)
        self.ctx = SkillContext(config, self.db, self.memory, self.personality)
        self.skills = SkillSet.load(config.skills, self.ctx)

        self.session_id: int | None = None
        self.system_prompt = ""
        self.messages: list[dict[str, Any]] = []   # conversación tal como se envía a Claude
        self._first_turn = True
        self._briefing = ""
        self._turns_since_reflection = 0

    @property
    def name(self) -> str:
        return self.personality.name

    # ------------------------------------------------------------------
    # Sesiones
    # ------------------------------------------------------------------

    def pending_reflections(self) -> list[int]:
        """Sesiones anteriores que no alcanzaron a consolidarse (p. ej. si se cerró de golpe)."""
        return self.db.sessions_pending_reflection(exclude=self.session_id)

    def consolidate_pending(self) -> int:
        done = 0
        for sid in self.pending_reflections():
            try:
                reflect_on_session(self.brain, self.db, self.memory, self.personality, sid)
                done += 1
            except Exception:  # noqa: BLE001 - sin conexión, sin saldo...: se reintenta la próxima vez
                break
        return done

    def start_session(self) -> None:
        self.session_id = self.db.start_session()
        self.ctx.session.clear()
        self.ctx.session["id"] = self.session_id
        last_seen = self.personality.register_session_start()
        summary = self.db.last_session_summary(self.session_id)
        self.system_prompt = build_system_prompt(self.personality, self.skills.guidance(), last_seen, summary)
        self.messages = self._previous_history()
        self._briefing = self.skills.briefing()
        self._first_turn = True
        self._turns_since_reflection = 0

    def _previous_history(self) -> list[dict[str, Any]]:
        rows = self.db.recent_messages(self.config.history_messages, before_session=self.session_id)
        history = [{"role": r["role"], "content": r["content"]} for r in rows]
        while history and history[0]["role"] != "user":
            history.pop(0)
        return history

    def end_session(self) -> Reflexion | None:
        """Consolida lo vivido y cierra la sesión. Llamar siempre al salir."""
        if self.session_id is None:
            return None
        result = None
        try:
            result = self.reflect()
        except Exception:  # noqa: BLE001 - queda pendiente y se consolida al volver
            pass
        self.db.end_session(self.session_id)
        self.personality.save()
        self.session_id = None
        return result

    def reflect(self) -> Reflexion | None:
        if self.session_id is None:
            return None
        self._turns_since_reflection = 0
        return reflect_on_session(self.brain, self.db, self.memory, self.personality, self.session_id)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------
    # Conversación
    # ------------------------------------------------------------------

    def _context_block(self, user_text: str) -> str:
        at = now()
        parts = [f"Ahora: {fecha_larga(at)} ({momento_del_dia(at)})."]
        if self._first_turn:
            if self.messages:
                parts.append("Empieza una conversación nueva; los mensajes anteriores son de la vez pasada.")
            if self._briefing:
                parts.append(self._briefing)
            self._first_turn = False
        extra = self.skills.turn_context(user_text)
        if extra:
            parts.append(extra)
        return "<contexto_ali>\n" + "\n\n".join(parts) + "\n</contexto_ali>"

    def greet(self) -> Iterator[Event]:
        """Ali abre la conversación ella misma."""
        yield from self._turn(GREETING_PROMPT, (), persist_user=False, query="")

    def chat(self, user_text: str, images: Iterable[Path] = ()) -> Iterator[Event]:
        """Envía un mensaje a Ali y va entregando su respuesta en eventos."""
        yield from self._turn(user_text, tuple(images), persist_user=True)

    def _turn(
        self, user_text: str, images: tuple[Path, ...], persist_user: bool, query: str | None = None
    ) -> Iterator[Event]:
        if self.session_id is None:
            self.start_session()

        # Si el turno falla, se restaura este estado para no perder el briefing ni los recuerdos.
        saved_state = (self._first_turn, {k: set(v) if isinstance(v, set) else v for k, v in self.ctx.session.items()})
        context = self._context_block(user_text if query is None else query)
        content: list[dict[str, Any]] = [{"type": "text", "text": context}]
        for path in images:
            try:
                content.append(image_block(path))
            except (ValueError, OSError) as e:
                self._restore(saved_state)
                yield Event("error", f"No pude abrir la imagen {path}: {e}")
                return
        content.append({"type": "text", "text": user_text})

        checkpoint = len(self.messages)
        self.messages.append({"role": "user", "content": content})
        reply: list[str] = []
        mark = 0
        refused = False
        try:
            for event in self.brain.run_turn(
                self.system_prompt, self.messages, self.skills.api_tools(), self.skills.execute
            ):
                if event.kind == "round":
                    if reply and not reply[-1].endswith(("\n", " ")):
                        reply.append("\n")
                    mark = len(reply)
                elif event.kind == "retry":
                    del reply[mark:]
                elif event.kind == "text":
                    reply.append(event.text)
                elif event.kind == "refusal":
                    refused = True
                yield event
        except Exception as e:  # noqa: BLE001 - ningún error debe tumbar la conversación
            del self.messages[checkpoint:]  # el turno no ocurrió: la conversación queda como estaba
            self._restore(saved_state)
            yield Event("error", friendly_error(e, self.config.model))
            return
        if refused:
            del self.messages[checkpoint:]
            self._restore(saved_state)
            return

        text = "".join(reply).strip()
        if persist_user:
            stored = user_text + "".join(f" [imagen: {Path(p).name}]" for p in images)
            self.db.add_message(self.session_id, "user", stored)
            self.personality.register_message()
            self._turns_since_reflection += 1
        if text:
            self.db.add_message(self.session_id, "assistant", text)
        self.personality.save()
        self._trim_history()

        if self.config.reflect_every and self._turns_since_reflection >= self.config.reflect_every:
            yield Event("notice", f"({self.name} se queda pensando en lo que han hablado…)")
            try:
                self.reflect()
            except Exception as e:  # noqa: BLE001 - queda pendiente para después
                yield Event("error", friendly_error(e, self.config.model))

    def _restore(self, saved_state: tuple[bool, dict[str, Any]]) -> None:
        self._first_turn = saved_state[0]
        self.ctx.session.clear()
        self.ctx.session.update(saved_state[1])

    def _trim_history(self) -> None:
        """Si la sesión es muy larga, olvida lo más viejo del contexto (sigue en la memoria)."""
        limit = self.config.max_session_messages
        if len(self.messages) <= limit:
            return
        for i in range(len(self.messages) - limit // 2, len(self.messages)):
            m = self.messages[i]
            if m["role"] == "user" and not _is_tool_result(m):
                self.messages = self.messages[i:]
                self.ctx.session["surfaced"] = set()  # esos recuerdos pueden volver a aparecer
                return

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def export(self, path: Path | None = None) -> Path:
        """Exporta todo lo que Ali sabe y es a un JSON legible (respaldo)."""
        path = path or self.config.data_dir / "exportaciones" / f"ali_{now():%Y%m%d_%H%M%S}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tables = {}
        for table in ("memories", "sessions", "events", "media"):
            try:
                tables[table] = [dict(r) for r in self.db.query(f"SELECT * FROM {table}")]
            except Exception:
                continue
        for m in tables.get("memories", []):
            m.pop("tokens", None)
        data = {"exportado": iso(now()), "personalidad": self.personality.snapshot(), **tables}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
