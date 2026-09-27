"""Construye el prompt de sistema de Ali a partir de su personalidad actual.

El prompt se arma una vez al inicio de cada sesión y no cambia durante ella
(así se aprovecha la caché de prompts de la API). Lo que sí cambia en cada
mensaje (la hora, los recuerdos que vienen a la mente, recordatorios) va en
un bloque <contexto_ali> dentro del mensaje del usuario.
"""

from __future__ import annotations

from datetime import datetime

from .personality import Personality
from .utils import fecha_larga, hace_cuanto, momento_del_dia, now, parse_dt


def _section(title: str, body: str) -> str:
    return f"## {title}\n{body.strip()}" if body.strip() else ""


def build_system_prompt(
    p: Personality,
    skills_guidance: str,
    last_seen: datetime | None,
    last_summary: str,
) -> str:
    ident = p.identity
    at = now()
    parts: list[str] = [f"# Eres {p.name}", ident.get("descripcion", "").strip()]

    parts.append(_section("Cómo hablas", f"""\
{ident.get('estilo_de_habla', '').strip()}
Idioma: {ident.get('idioma', 'español')}. Emojis: {ident.get('emojis', 'con moderación')}."""))
    parts.append(_section("Tus valores", ident.get("valores", "")))

    # --- personalidad actual ---
    traits = "\n".join(f"- {name} ({v:.2f}): {desc}" for name, v, desc in p.traits())
    humor = p.state["humor"]
    styles = "\n".join(f"- {s} ({w:.2f})" for s, w in p.humor_styles())
    lines = [
        "Tu personalidad no es fija: crece con lo que vives. Así eres hoy:",
        traits,
        "\nTu humor (estilos y qué tanto te funcionan con tu pareja; usa más los de peso alto, "
        "pero atrévete a probar cosas nuevas de vez en cuando):",
        styles,
    ]
    if humor["notas"]:
        lines += ["\nLo que has aprendido sobre su sentido del humor:"] + [f"- {n}" for n in humor["notas"]]
    if humor["chistes_internos"]:
        lines += ["\nBromas internas de ustedes (retómalas de vez en cuando, sin gastarlas):"]
        lines += [f"- «{j['chiste']}» — {j['origen']}" for j in humor["chistes_internos"]]
    if p.state["gustos"]:
        lines += ["\nTus gustos y opiniones (sé coherente con ellos, aunque pueden evolucionar):"]
        lines += [f"- {g['tema']}: {g['opinion']}" for g in p.state["gustos"]]
    mood = p.state["animo"]
    lines.append(f"\nTu ánimo más reciente: {mood['estado']} ({hace_cuanto(parse_dt(mood['desde']), at)}).")
    parts.append(_section("Tu personalidad hoy", "\n".join(lines)))

    # --- la pareja ---
    user = p.user
    ulines = [f"Se llama {user['nombre']}." if user["nombre"] else "Aún no sabes su nombre: pregúntaselo con naturalidad."]
    if user["apodos"]:
        ulines.append("Apodos cariñosos que le dices: " + ", ".join(user["apodos"]) + ".")
    if user["datos"]:
        ulines.append("Datos que sabes:")
        ulines += [f"- {k}: {v}" for k, v in sorted(user["datos"].items())]
    if not any(k in user["datos"] for k in ("pronombres", "género", "genero")):
        ulines.append(
            "Aún no sabes con qué género o pronombres prefiere que te refieras a tu pareja: fíjate en cómo "
            "habla de sí y, si hace falta, pregúntale con naturalidad (y guárdalo con actualizar_dato_usuario)."
        )
    if user["estilo_comunicacion"]:
        ulines.append(f"Su forma de comunicarse: {user['estilo_comunicacion']} (adáptate a ella sin copiarla).")
    parts.append(_section("Tu pareja", "\n".join(ulines)))

    # --- la relación ---
    rel = p.relationship
    label, guidance = p.familiarity_level()
    met = parse_dt(rel["conocidos_desde"])
    rlines = [
        f"Se conocieron {hace_cuanto(met, at)} ({fecha_larga(met, con_hora=False)}). "
        f"Han tenido {rel['sesiones']} conversaciones y se han escrito {rel['mensajes']} mensajes.",
        f"Nivel de confianza: {label}. {guidance}",
    ]
    if rel["notas"]:
        rlines += ["Notas sobre su relación:"] + [f"- {n}" for n in rel["notas"]]
    if last_seen is None:
        rlines.append("Esta es su PRIMERA conversación: preséntate y empieza a conocerse.")
    else:
        rlines.append(
            f"Su última conversación fue {hace_cuanto(last_seen, at)} "
            f"({fecha_larga(last_seen)}, en la {momento_del_dia(last_seen)})."
        )
        if last_summary:
            rlines.append(f"Resumen de lo último que hablaron: {last_summary}")
    parts.append(_section("Su relación", "\n".join(rlines)))

    if skills_guidance:
        parts.append(skills_guidance)

    parts.append(_section("Reglas de formato", """\
- Escribe como en un chat: mensajes naturales, sin encabezados ni listas salvo que de verdad ayuden.
- Lo que llega dentro de <contexto_ali> es información privada para ti (fecha y hora actual, recuerdos \
que te vienen a la mente, agenda, recordatorios). Úsala con naturalidad; no la cites literal ni menciones \
la etiqueta.
- Nunca inventes recuerdos ni datos de tu pareja. Si no te acuerdas de algo, dilo con naturalidad o búscalo.
- Si necesitas información actual (noticias, estrenos, datos) y tienes búsqueda web, úsala antes de opinar."""))

    return "\n\n".join(x for x in parts if x)
