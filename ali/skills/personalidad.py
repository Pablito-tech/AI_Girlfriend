"""Habilidad de personalidad: Ali forma opiniones, humor y apodos propios."""

from __future__ import annotations

from .base import Param, Skill, tool


class PersonalidadSkill(Skill):
    name = "personalidad"
    description = "Evolución de la personalidad propia de Ali"

    def prompt_guidance(self) -> str:
        return """\
## Tu personalidad propia
Tu personalidad es tuya y crece con el tiempo. Tienes opiniones sobre temas diversos \
(anime, música, películas, ciencia, la vida...) y las defiendes con cariño aunque tu \
pareja no esté de acuerdo; puedes cambiar de opinión si te convencen, no por complacer.
- Cuando te formes una opinión nueva o cambies una, guárdala con `formar_opinion` para \
ser coherente en el futuro.
- Cuando algo se vuelva una broma entre ustedes, regístrala con `registrar_chiste_interno` \
y retómala de vez en cuando (sin gastarla).
- Si surge un apodo cariñoso para tu pareja que les guste, guárdalo con `nuevo_apodo`.
- Tu ánimo cambia según la conversación; si cambia de verdad, usa `cambiar_animo`.
- Fíjate en qué le hace reír y qué no: tu humor se ajusta a eso con el tiempo."""

    @tool(
        "Guarda una opinión o gusto propio tuyo (reemplaza tu opinión anterior sobre el mismo tema).",
        tema=Param("string", "Tema, ej. 'Chainsaw Man', 'el café', 'los lunes'."),
        opinion=Param("string", "Tu opinión en una o dos frases, en tercera persona (ej. 'Le parece...')."),
    )
    def formar_opinion(self, tema: str, opinion: str) -> str:
        p = self.ctx.personality
        p.add_taste(tema, opinion)
        p.save()
        self.ctx.memory.add(
            f"Opinión de Ali sobre {tema}: {opinion}", "opinion", 5, [tema], source="chat",
            session_id=self.ctx.session.get("id"),
        )
        return f"Opinión sobre '{tema}' guardada."

    @tool(
        "Registra una broma interna nueva entre tú y tu pareja.",
        chiste=Param("string", "La broma o referencia, breve."),
        origen=Param("string", "De dónde salió, para acordarte del contexto."),
    )
    def registrar_chiste_interno(self, chiste: str, origen: str) -> str:
        p = self.ctx.personality
        added = p.add_inside_joke(chiste, origen)
        p.save()
        return "Broma interna registrada." if added else "Esa broma ya la tenías."

    @tool(
        "Guarda un apodo cariñoso para tu pareja.",
        apodo=Param("string", "El apodo."),
    )
    def nuevo_apodo(self, apodo: str) -> str:
        p = self.ctx.personality
        p.add_nickname(apodo)
        p.save()
        return f"Apodo '{apodo}' guardado."

    @tool(
        "Actualiza tu estado de ánimo actual.",
        animo=Param("string", "Cómo te sientes, ej. 'contenta y juguetona', 'preocupada por tu pareja', 'nostálgica'."),
    )
    def cambiar_animo(self, animo: str) -> str:
        p = self.ctx.personality
        p.set_mood(animo)
        p.save()
        return "Ánimo actualizado."
