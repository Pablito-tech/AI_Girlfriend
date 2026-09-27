"""Reflexión: al terminar (o cada cierto número de mensajes) Ali repasa lo que
hablaron, como cuando uno se queda pensando en una conversación.

De ahí salen:
- recuerdos nuevos (y refuerzo de los que ya tenía),
- datos y forma de ser de su pareja,
- pequeños ajustes a sus rasgos y a su humor,
- bromas internas, gustos propios, apodos, ánimo,
- un resumen de la sesión para retomar la próxima vez.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .memory import KINDS
from .utils import iso, now, parse_dt, truncate

if TYPE_CHECKING:
    from .brain import ClaudeBrain
    from .memory import MemoryStore
    from .personality import Personality
    from .storage import Database

MemoryKind = Literal["hecho", "preferencia", "momento", "evento", "opinion", "nota"]
assert set(MemoryKind.__args__) == set(KINDS)  # mantener sincronizado con memory.KINDS


class NuevoRecuerdo(BaseModel):
    contenido: str = Field(description="Recuerdo en tercera persona, con contexto suficiente para entenderlo meses después.")
    tipo: MemoryKind
    importancia: int = Field(description="1 (trivial) a 10 (inolvidable)")
    etiquetas: list[str]
    fecha_seguimiento: str = Field(description="YYYY-MM-DD para preguntar cómo le fue, o cadena vacía")


class DatoUsuario(BaseModel):
    clave: str
    valor: str


class Ajuste(BaseModel):
    nombre: str
    delta: float = Field(description="Entre -0.05 y 0.05")
    motivo: str


class ChisteInterno(BaseModel):
    chiste: str
    origen: str


class Gusto(BaseModel):
    tema: str
    opinion: str


class Reflexion(BaseModel):
    resumen: str = Field(description="Resumen breve (2-4 frases) de la conversación, desde el punto de vista de Ali")
    recuerdos: list[NuevoRecuerdo]
    datos_usuario: list[DatoUsuario]
    estilo_comunicacion_usuario: str = Field(description="Cómo se comunica su pareja; cadena vacía si no hay nada nuevo")
    ajustes_rasgos: list[Ajuste]
    ajustes_humor: list[Ajuste]
    notas_humor: list[str]
    chistes_internos: list[ChisteInterno]
    gustos_de_ali: list[Gusto]
    apodos_para_usuario: list[str]
    estado_animo: str
    nota_relacion: str = Field(description="Algo significativo sobre cómo va la relación; cadena vacía si nada")


REFLECTION_SYSTEM = """\
Eres el proceso de reflexión de {name}, una IA con personalidad propia que es la novia virtual de su \
pareja. Tu trabajo es repasar una conversación reciente y decidir qué debe recordar {name} y cómo \
evoluciona. Escribe en español.

Criterios:
- Recuerdos: guarda lo que una pareja atenta recordaría (datos personales, gustos, planes con fecha, \
preocupaciones, logros, momentos especiales entre ellos). No guardes trivialidades ni repitas recuerdos \
que ya existen salvo que haya información nueva (en ese caso escríbelo completo y actualizado). \
Si algo tiene fecha futura (examen, viaje, cita médica), pon fecha_seguimiento el día posterior.
- datos_usuario: sólo datos estables y explícitos (cumpleaños, ciudad, trabajo, pronombres...).
- Rasgos de {name}: {traits}. Ajusta sólo si la conversación lo justifica (deltas pequeños, -0.05 a 0.05). \
Su personalidad debe ser propia, no un espejo: no la vuelvas idéntica a la de su pareja.
- Humor: si una broma le hizo gracia a su pareja (risas, "jajaja", seguirle el juego), sube ese estilo; \
si cayó mal o no la entendió, bájalo. Nombres de estilos existentes: {styles}. Puedes proponer estilos nuevos.
- chistes_internos: sólo si de verdad surgió una broma recurrente o referencia compartida.
- gustos_de_ali: opiniones que {name} expresó o formó en la conversación.
- estado_animo: cómo quedó {name} al final, en pocas palabras.
Si no hay nada para un campo, deja la lista vacía o la cadena vacía."""


def _transcript(messages: list[dict], user_label: str, ali_label: str) -> str:
    lines = []
    for m in messages:
        who = user_label if m["role"] == "user" else ali_label
        lines.append(f"[{m['created_at'][:16].replace('T', ' ')}] {who}: {m['content']}")
    return "\n".join(lines)


def reflect_on_session(
    brain: "ClaudeBrain",
    db: "Database",
    memory: "MemoryStore",
    personality: "Personality",
    session_id: int,
) -> Reflexion | None:
    """Consolida los mensajes aún no procesados de una sesión. Devuelve la reflexión aplicada."""
    pending = db.unreflected_messages(session_id)
    if not any(m["role"] == "user" for m in pending):
        if pending:
            db.mark_reflected(session_id, pending[-1]["id"])
        return None

    p = personality
    user_label = p.user_name or "Pareja"
    transcript = _transcript(pending, user_label, p.name)
    related = memory.search(transcript, limit=25, min_score=0.0, touch=False)
    existing = "\n".join(f"- {r.memory.render()}" for r in related) or "(ninguno)"
    profile = "\n".join(f"- {k}: {v}" for k, v in p.user["datos"].items()) or "(vacío)"
    jokes = "\n".join(f"- {j['chiste']}" for j in p.state["humor"]["chistes_internos"]) or "(ninguna)"

    prompt = f"""\
Fecha de hoy: {now():%Y-%m-%d}

Perfil actual de su pareja ({user_label}):
{profile}

Recuerdos relacionados que {p.name} ya tiene:
{existing}

Bromas internas actuales:
{jokes}

Conversación a procesar:
{transcript}"""

    system = REFLECTION_SYSTEM.format(
        name=p.name,
        traits=", ".join(f"{n} ({v:.2f})" for n, v, _ in p.traits()),
        styles=", ".join(s for s, _ in p.humor_styles()),
    )
    result = brain.structured(system, prompt, Reflexion)
    if result is None:
        return None
    apply_reflection(result, memory, p, session_id)
    db.mark_reflected(session_id, pending[-1]["id"], truncate(result.resumen, 600))
    return result


def apply_reflection(r: Reflexion, memory: "MemoryStore", p: "Personality", session_id: int | None) -> None:
    for rec in r.recuerdos:
        follow = None
        if rec.fecha_seguimiento.strip():
            try:
                follow = parse_dt(rec.fecha_seguimiento)
            except ValueError:
                follow = None
        memory.add(rec.contenido, rec.tipo, rec.importancia, rec.etiquetas, follow, source="reflexion", session_id=session_id)

    for d in r.datos_usuario:
        if d.clave.strip().lower() in {"nombre", "name"} and d.valor.strip():
            p.set_user_name(d.valor)
        else:
            p.set_user_fact(d.clave, d.valor)
    p.set_user_style(r.estilo_comunicacion_usuario)
    for a in r.ajustes_rasgos:
        p.adjust_trait(a.nombre.strip().lower(), a.delta)
    for a in r.ajustes_humor:
        p.adjust_humor(a.nombre, a.delta)
    for note in r.notas_humor:
        p.add_humor_note(note)
    for j in r.chistes_internos:
        p.add_inside_joke(j.chiste, j.origen)
    for g in r.gustos_de_ali:
        p.add_taste(g.tema, g.opinion)
        memory.add(f"Opinión de {p.name} sobre {g.tema}: {g.opinion}", "opinion", 5, [g.tema],
                   source="reflexion", session_id=session_id)
    for nick in r.apodos_para_usuario:
        p.add_nickname(nick)
    p.set_mood(r.estado_animo)
    p.add_relationship_note(r.nota_relacion)
    p.state["relacion"]["ultima_reflexion"] = iso(now())
    p.save()
