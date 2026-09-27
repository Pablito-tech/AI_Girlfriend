"""Habilidad de agenda: horarios, pendientes y recordatorios."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..utils import DIAS, iso, now, parse_dt
from .base import Param, Skill, ToolError, tool

REMINDER_WINDOW = timedelta(minutes=60)


def _fmt(row, at: datetime) -> str:
    start = parse_dt(row["starts_at"])
    when = f"{DIAS[start.weekday()]} {start:%d/%m %H:%M}"
    if start.date() == at.date():
        when = f"hoy {start:%H:%M}"
    elif start.date() == (at + timedelta(days=1)).date():
        when = f"mañana {start:%H:%M}"
    status = " ✓" if row["done"] else (" (atrasado)" if start < at else "")
    notes = f" — {row['notes']}" if row["notes"] else ""
    return f"[{row['id']}] {when}: {row['title']}{status}{notes}"


class AgendaSkill(Skill):
    name = "agenda"
    description = "Agenda de eventos y pendientes"

    def setup(self) -> None:
        self.ctx.db.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                starts_at   TEXT NOT NULL,
                duration_min INTEGER,
                notes       TEXT NOT NULL DEFAULT '',
                done        INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_start ON events(starts_at);
            """
        )

    def prompt_guidance(self) -> str:
        return """\
## Agenda
Llevas la agenda de tu pareja: eventos, citas, entregas y pendientes. Cuando te \
mencione algo con fecha y hora, ofrécete a anotarlo (o anótalo si es claro que lo quiere). \
Calcula las fechas a partir de la fecha actual que ves en <contexto_ali>. Si falta la hora, \
pregúntala o usa una razonable y díselo. Recuérdale lo próximo con naturalidad."""

    # ------------------------------------------------------------------

    def _get(self, event_id: int):
        row = self.ctx.db.query_one("SELECT * FROM events WHERE id = ?", (event_id,))
        if not row:
            raise ToolError(f"No existe el evento {event_id}.")
        return row

    @tool(
        "Agrega un evento o pendiente a la agenda.",
        titulo=Param("string", "Qué es."),
        fecha_hora=Param("string", "Fecha y hora ISO: YYYY-MM-DDTHH:MM (o YYYY-MM-DD para todo el día)."),
        duracion_min=Param("integer", "Duración en minutos.", required=False),
        notas=Param("string", "Detalles extra.", required=False),
    )
    def agregar_evento(self, titulo: str, fecha_hora: str, duracion_min=None, notas=""):
        try:
            start = parse_dt(fecha_hora)
        except ValueError:
            raise ToolError("fecha_hora no es válida; usa el formato YYYY-MM-DDTHH:MM.")
        event_id = self.ctx.db.execute(
            "INSERT INTO events (title, starts_at, duration_min, notes, created_at) VALUES (?, ?, ?, ?, ?)",
            (titulo.strip(), iso(start), duracion_min, notas or "", iso(now())),
        ).lastrowid
        return "Anotado: " + _fmt(self._get(event_id), now())

    @tool(
        "Muestra la agenda en un rango de fechas (por defecto: pendientes atrasados y los próximos 7 días).",
        desde=Param("string", "Fecha ISO de inicio.", required=False),
        hasta=Param("string", "Fecha ISO de fin (inclusive).", required=False),
        incluir_completados=Param("boolean", "Mostrar también los ya completados.", required=False),
    )
    def ver_agenda(self, desde=None, hasta=None, incluir_completados=False):
        at = now()
        try:
            start = parse_dt(desde) if desde else at - timedelta(days=7)
            end = parse_dt(hasta) if hasta else at + timedelta(days=7)
        except ValueError:
            raise ToolError("Fechas inválidas; usa YYYY-MM-DD.")
        if hasta and len(hasta.strip()) <= 10:
            end = end.replace(hour=23, minute=59, second=59)
        rows = self.ctx.db.query(
            "SELECT * FROM events WHERE starts_at BETWEEN ? AND ? ORDER BY starts_at", (iso(start), iso(end))
        )
        rows = [
            r for r in rows
            if incluir_completados or not r["done"]
        ]
        if not desde:  # en la vista por defecto, de lo pasado sólo interesa lo que quedó pendiente
            rows = [r for r in rows if parse_dt(r["starts_at"]) >= at.replace(hour=0, minute=0) or not r["done"]]
        if not rows:
            return "No hay nada en la agenda para esas fechas."
        return "\n".join(_fmt(r, at) for r in rows)

    @tool("Marca un evento o pendiente como completado.", id=Param("integer", "Número del evento."))
    def completar_evento(self, id: int):
        self._get(id)
        self.ctx.db.execute("UPDATE events SET done = 1 WHERE id = ?", (id,))
        return f"Evento {id} completado."

    @tool(
        "Cambia la fecha/hora, el título o las notas de un evento.",
        id=Param("integer", "Número del evento."),
        fecha_hora=Param("string", "Nueva fecha y hora ISO.", required=False),
        titulo=Param("string", "Nuevo título.", required=False),
        notas=Param("string", "Nuevas notas.", required=False),
    )
    def editar_evento(self, id: int, fecha_hora=None, titulo=None, notas=None):
        row = self._get(id)
        try:
            start = iso(parse_dt(fecha_hora)) if fecha_hora else row["starts_at"]
        except ValueError:
            raise ToolError("fecha_hora no es válida.")
        self.ctx.db.execute(
            "UPDATE events SET starts_at = ?, title = ?, notes = ? WHERE id = ?",
            (start, titulo or row["title"], row["notes"] if notas is None else notas, id),
        )
        return "Actualizado: " + _fmt(self._get(id), now())

    @tool("Elimina un evento de la agenda.", id=Param("integer", "Número del evento."))
    def eliminar_evento(self, id: int):
        self._get(id)
        self.ctx.db.execute("DELETE FROM events WHERE id = ?", (id,))
        return f"Evento {id} eliminado."

    # ------------------------------------------------------------------

    def session_briefing(self) -> str:
        at = now()
        overdue = self.ctx.db.query(
            "SELECT * FROM events WHERE done = 0 AND starts_at < ? AND starts_at >= ? ORDER BY starts_at",
            (iso(at), iso(at - timedelta(days=7))),
        )
        upcoming = self.ctx.db.query(
            "SELECT * FROM events WHERE done = 0 AND starts_at BETWEEN ? AND ? ORDER BY starts_at",
            (iso(at), iso(at + timedelta(hours=48))),
        )
        lines = []
        if upcoming:
            lines.append("Agenda próxima (48 h):")
            lines += [f"- {_fmt(r, at)}" for r in upcoming]
        if overdue:
            lines.append("Pendientes atrasados sin marcar como hechos (pregunta si ya los hizo):")
            lines += [f"- {_fmt(r, at)}" for r in overdue]
        return "\n".join(lines)

    def turn_context(self, user_text: str) -> str:
        at = now()
        reminded = self.ctx.session.setdefault("reminded_events", set())
        rows = self.ctx.db.query(
            "SELECT * FROM events WHERE done = 0 AND starts_at BETWEEN ? AND ? ORDER BY starts_at",
            (iso(at), iso(at + REMINDER_WINDOW)),
        )
        rows = [r for r in rows if r["id"] not in reminded]
        if not rows:
            return ""
        reminded.update(r["id"] for r in rows)
        return "⏰ Está por empezar (recuérdaselo):\n" + "\n".join(f"- {_fmt(r, at)}" for r in rows)
