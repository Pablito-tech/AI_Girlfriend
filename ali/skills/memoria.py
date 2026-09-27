"""Habilidad de memoria: Ali guarda, busca y recuerda activamente."""

from __future__ import annotations

import random

from ..memory import KINDS
from ..utils import hace_cuanto, now, parse_dt
from .base import Param, Skill, ToolError, tool

SPONTANEOUS_RECALL_CHANCE = 0.35


class MemoriaSkill(Skill):
    name = "memoria"
    description = "Memoria a largo plazo de Ali"

    def prompt_guidance(self) -> str:
        tipos = "\n".join(f"  - {k}: {desc}" for k, (desc, _) in KINDS.items())
        return f"""\
## Tu memoria
Tienes memoria a largo plazo que persiste entre conversaciones. Antes de cada mensaje \
te llegan los recuerdos relevantes dentro de <contexto_ali>. Úsalos con naturalidad, \
como lo haría alguien que de verdad se acuerda (sin decir "según mis registros").
- Usa `guardar_recuerdo` cuando tu pareja te cuente algo que valga la pena recordar: \
datos personales, gustos, planes, preocupaciones, logros, momentos bonitos entre ustedes. \
Si es algo con fecha (un examen, una entrevista, un viaje), pon `fecha_seguimiento` para \
preguntarle después cómo le fue.
- Usa `buscar_recuerdos` si necesitas acordarte de algo que no apareció en el contexto.
- Usa `actualizar_dato_usuario` para datos básicos y estables (cumpleaños, ciudad, trabajo...).
- No anuncies cada vez que guardas algo; hazlo en silencio salvo que te lo pidan.
- Al terminar cada conversación, repasas lo vivido y consolidas recuerdos automáticamente.
Tipos de recuerdo:
{tipos}"""

    # ------------------------------------------------------------------

    @tool(
        "Guarda un recuerdo a largo plazo. Si ya existe uno muy parecido, se actualiza y se refuerza.",
        contenido=Param("string", "El recuerdo, escrito en tercera persona y con contexto suficiente para entenderlo meses después."),
        tipo=Param("string", "Tipo de recuerdo.", enum=list(KINDS)),
        importancia=Param("integer", "1 (trivial) a 10 (inolvidable)."),
        etiquetas=Param("array", "Palabras clave para encontrarlo después.", required=False),
        fecha_seguimiento=Param(
            "string",
            "Fecha ISO (YYYY-MM-DD o YYYY-MM-DDTHH:MM) a partir de la cual conviene preguntar cómo salió.",
            required=False,
        ),
    )
    def guardar_recuerdo(self, contenido: str, tipo: str, importancia: int, etiquetas=None, fecha_seguimiento=None):
        follow = None
        if fecha_seguimiento:
            try:
                follow = parse_dt(fecha_seguimiento)
            except ValueError:
                raise ToolError("fecha_seguimiento no es una fecha ISO válida (usa YYYY-MM-DD).")
        mem, new = self.ctx.memory.add(
            contenido, tipo, importancia, etiquetas or [], follow, source="chat",
            session_id=self.ctx.session.get("id"),
        )
        self.ctx.session.setdefault("surfaced", set()).add(mem.id)
        return f"{'Guardado' if new else 'Actualizado (ya existía algo parecido)'}: recuerdo #{mem.id}."

    @tool(
        "Busca en tu memoria a largo plazo.",
        consulta=Param("string", "Qué quieres recordar, en palabras clave."),
        tipo=Param("string", "Filtrar por tipo de recuerdo.", required=False, enum=list(KINDS)),
        limite=Param("integer", "Máximo de resultados (por defecto 8).", required=False),
    )
    def buscar_recuerdos(self, consulta: str, tipo=None, limite=None):
        results = self.ctx.memory.search(consulta, limit=max(1, min(20, limite or 8)), kind=tipo, min_score=0.0)
        if not results:
            return "No encontraste nada sobre eso en tu memoria."
        self.ctx.session.setdefault("surfaced", set()).update(r.memory.id for r in results)
        return "\n".join(r.memory.render() for r in results)

    @tool(
        "Olvida (borra) un recuerdo. Úsalo sólo si tu pareja te lo pide explícitamente o si el recuerdo es incorrecto.",
        id=Param("integer", "Número del recuerdo (#id)."),
    )
    def olvidar_recuerdo(self, id: int):
        if not self.ctx.memory.delete(id):
            raise ToolError(f"No existe el recuerdo #{id}.")
        return f"Recuerdo #{id} olvidado."

    @tool(
        "Guarda o actualiza un dato básico y estable de tu pareja (siempre lo tendrás presente). Deja 'valor' vacío para borrarlo.",
        clave=Param("string", "Nombre del dato, ej. 'cumpleaños', 'ciudad', 'trabajo', 'mascota'."),
        valor=Param("string", "El dato."),
    )
    def actualizar_dato_usuario(self, clave: str, valor: str):
        p = self.ctx.personality
        if clave.strip().lower() in {"nombre", "name"} and valor.strip():
            p.set_user_name(valor)
        else:
            p.set_user_fact(clave, valor)
        p.save()
        return f"Dato '{clave}' actualizado."

    # ------------------------------------------------------------------

    def session_briefing(self) -> str:
        memory, at = self.ctx.memory, now()
        surfaced = self.ctx.session.setdefault("surfaced", set())
        lines: list[str] = []

        def show(memories):
            surfaced.update(m.id for m in memories)
            return [f"- {m.render(at)}" for m in memories]

        due = memory.due_follow_ups(at)
        if due:
            lines.append("Cosas sobre las que conviene preguntarle cómo le fue (con naturalidad, no todas de golpe):")
            lines += show(due[:5])
            memory.mark_followed_up(m.id for m in due[:5])

        anniversaries = memory.anniversaries(at)
        if anniversaries:
            lines.append("Hoy se cumplen años de estos recuerdos:")
            lines += show(anniversaries[:3])

        since = self.ctx.personality.relationship.get("conocidos_desde")
        if since:
            met = parse_dt(since)
            if met.year < at.year and (met.month, met.day) == (at.month, at.day):
                lines.append(f"¡Hoy es su aniversario! Se conocieron {hace_cuanto(met, at)}.")

        if random.random() < SPONTANEOUS_RECALL_CHANCE:
            fading = memory.fading_important(at)
            if fading:
                lines.append(
                    "Un recuerdo bonito que se te estaba desvaneciendo y que podrías sacar si viene al caso:"
                )
                lines += show([fading])
                memory.touch([fading.id])

        return "\n".join(lines)

    def turn_context(self, user_text: str) -> str:
        surfaced = self.ctx.session.setdefault("surfaced", set())
        results = self.ctx.memory.search(
            user_text, limit=self.ctx.config.memories_per_turn, exclude_ids=surfaced, strict=True
        )
        if not results:
            return ""
        surfaced.update(r.memory.id for r in results)
        return "Recuerdos que te vienen a la mente:\n" + "\n".join(f"- {r.memory.render()}" for r in results)
