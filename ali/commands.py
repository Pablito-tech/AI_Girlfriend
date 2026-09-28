"""Comandos compartidos por todas las interfaces (terminal, Telegram...).

Cada función recibe a Ali y devuelve texto listo para mostrar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .core import friendly_error
from .utils import hace_cuanto, parse_dt

if TYPE_CHECKING:
    from .core import Ali


def memories(ali: "Ali", query: str = "") -> str:
    if query:
        mems = [r.memory for r in ali.memory.search(query, limit=15, min_score=0.0, touch=False)]
    else:
        mems = ali.memory.recent(15)
    body = "\n".join(m.render() for m in mems) or "Aún no hay recuerdos."
    return f"{body}\n({ali.memory.count()} recuerdos en total)"


def forget(ali: "Ali", arg: str) -> str:
    arg = arg.strip().lstrip("#")
    if not arg.isdigit():
        return "Uso: /olvidar <id>"
    return "Olvidado." if ali.memory.delete(int(arg)) else "No existe ese recuerdo."


def run_tool(ali: "Ali", name: str, args: dict | None = None) -> str:
    if not ali.skills.get(name):
        return "Esa habilidad no está activada."
    output, _ = ali.skills.execute(name, args or {})
    return output if isinstance(output, str) else str(output)


def profile(ali: "Ali") -> str:
    u = ali.personality.user
    lines = [f"Nombre: {u['nombre'] or '(desconocido)'}"]
    if u["apodos"]:
        lines.append("Apodos: " + ", ".join(u["apodos"]))
    lines += [f"- {k}: {v}" for k, v in sorted(u["datos"].items())]
    if u["estilo_comunicacion"]:
        lines.append(f"Tu estilo: {u['estilo_comunicacion']}")
    return "\n".join(lines)


def personality(ali: "Ali") -> str:
    p = ali.personality
    label, _ = p.familiarity_level()
    rel = p.relationship
    sesiones, mensajes = rel["sesiones"], rel["mensajes"]
    lines = [
        f"{p.name} — confianza: {label} ({p.familiarity():.2f})",
        f"Se conocieron {hace_cuanto(parse_dt(rel['conocidos_desde']))} · "
        f"{sesiones} conversaci{'ón' if sesiones == 1 else 'ones'} · "
        f"{mensajes} mensaje{'' if mensajes == 1 else 's'}",
        f"Ánimo: {p.state['animo']['estado']}",
        "Rasgos:",
    ]
    for name, v, desc in p.traits():
        bar = "█" * round(v * 10) + "░" * (10 - round(v * 10))
        lines.append(f"  {name:<11} {bar} {v:.2f}  {desc}")
    lines.append("Humor:")
    lines += [f"  {w:.2f}  {style}" for style, w in p.humor_styles()]
    jokes = p.state["humor"]["chistes_internos"]
    if jokes:
        lines.append("Bromas internas: " + " · ".join(j["chiste"] for j in jokes))
    tastes = p.state["gustos"]
    lines.append(
        f"Gustos propios: {len(tastes)} (el más reciente: {tastes[-1]['tema']})" if tastes
        else "Gustos propios: ninguno aún"
    )
    return "\n".join(lines)


def reflect(ali: "Ali") -> str:
    try:
        r = ali.reflect()
    except Exception as e:  # noqa: BLE001
        return friendly_error(e, ali.config.model)
    return f"Listo: {len(r.recuerdos)} recuerdos consolidados." if r else "No había nada nuevo."
