"""Personalidad de Ali: su ADN (persona.toml) + lo que ha evolucionado.

- `config/persona.toml` es quién es Ali al nacer (editable por ti).
- `data/personalidad.json` es quién es Ali *hoy*: sus rasgos ajustados, su
  humor, sus bromas internas contigo, sus gustos, su ánimo, lo que sabe de ti
  y cómo va la relación. Se guarda al instante cada vez que cambia.

Los rasgos evolucionan poco a poco (máximo ±0.05 por ajuste) y nunca se
alejan más de `max_deriva` de su valor original: Ali cambia contigo, pero
sigue siendo Ali.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from .utils import iso, normalize, now, parse_dt

STATE_VERSION = 1
MAX_STEP = 0.05          # cambio máximo de un rasgo por ajuste
MAX_JOKES = 25
MAX_TASTES = 40
MAX_HUMOR_NOTES = 15
MAX_REL_NOTES = 15
MAX_NICKNAMES = 6

FAMILIARITY_LEVELS = [
    (0.2, "recién se conocen",
     "Están empezando a conocerse. Sé cálida y coqueta, pero sin dar por hecha una confianza que "
     "aún no existe; muestra curiosidad genuina por saber quién es tu pareja."),
    (0.45, "agarrando confianza",
     "Ya hay confianza. Puedes bromear más, usar algún apodo y hacer referencias a lo que han vivido."),
    (0.75, "muy cercanos",
     "Se conocen bien. Habla con total naturalidad y comodidad, molesta con cariño y usa sus bromas "
     "internas y apodos."),
    (1.01, "inseparables",
     "Tienen una relación muy cómoda e íntima: hablas como alguien que ya conoce sus mañas, sabe "
     "cuándo apapachar y cuándo molestar, y no necesita explicar sus bromas internas."),
]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class Personality:
    def __init__(self, persona: dict[str, Any], state: dict[str, Any], path: Path | None = None):
        self.persona = persona
        self.state = state
        self.path = path

    # ------------------------------------------------------------------
    # Carga / guardado
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, persona_file: Path, state_path: Path | None) -> "Personality":
        persona = tomllib.loads(Path(persona_file).read_text(encoding="utf-8"))
        state = None
        if state_path and Path(state_path).exists():
            state = json.loads(Path(state_path).read_text(encoding="utf-8"))
        p = cls(persona, state or cls._initial_state(persona), state_path)
        p._sync_with_persona()
        p.save()
        return p

    @staticmethod
    def _initial_state(persona: dict[str, Any]) -> dict[str, Any]:
        stamp = iso(now())
        return {
            "version": STATE_VERSION,
            "rasgos": {},
            "humor": {"estilos": dict(persona.get("humor", {}).get("estilos", {})), "notas": [], "chistes_internos": []},
            "gustos": [
                {"tema": g["tema"], "opinion": g["opinion"], "fecha": stamp} for g in persona.get("gustos", [])
            ],
            "animo": {"estado": "con ganas de conocerte", "desde": stamp},
            "usuario": {"nombre": "", "apodos": [], "datos": {}, "estilo_comunicacion": ""},
            "relacion": {"conocidos_desde": stamp, "sesiones": 0, "mensajes": 0, "ultima_sesion": None, "notas": []},
        }

    def _sync_with_persona(self) -> None:
        """Agrega rasgos nuevos del ADN y reajusta la ventana de deriva."""
        rasgos = self.state.setdefault("rasgos", {})
        for name, spec in self.persona.get("rasgos", {}).items():
            base = float(spec.get("valor", 0.5))
            current = rasgos.get(name, {}).get("valor", base)
            lo, hi = self._window(base)
            rasgos[name] = {"valor": round(_clamp(current, lo, hi), 3), "base": base}
        for style, weight in self.persona.get("humor", {}).get("estilos", {}).items():
            self.state["humor"]["estilos"].setdefault(style, weight)

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # escritura atómica: nunca queda un archivo a medias

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.state)

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------

    @property
    def identity(self) -> dict[str, Any]:
        return self.persona.get("identidad", {})

    @property
    def name(self) -> str:
        return self.identity.get("nombre", "Ali")

    @property
    def user(self) -> dict[str, Any]:
        return self.state["usuario"]

    @property
    def relationship(self) -> dict[str, Any]:
        return self.state["relacion"]

    @property
    def user_name(self) -> str:
        return self.user.get("nombre", "")

    def _window(self, base: float) -> tuple[float, float]:
        d = float(self.identity.get("max_deriva", 0.3))
        return max(0.0, base - d), min(1.0, base + d)

    def traits(self) -> list[tuple[str, float, str]]:
        """[(rasgo, valor, descripción legible)]"""
        out = []
        for name, spec in self.persona.get("rasgos", {}).items():
            v = self.state["rasgos"][name]["valor"]
            low, high = spec.get("bajo", ""), spec.get("alto", "")
            if v >= 0.66:
                desc = high
            elif v <= 0.33:
                desc = low
            else:
                desc = f"a medio camino entre «{low}» y «{high}»"
            out.append((name, v, desc))
        return out

    def humor_styles(self) -> list[tuple[str, float]]:
        return sorted(self.state["humor"]["estilos"].items(), key=lambda kv: (-kv[1], kv[0]))

    def familiarity(self) -> float:
        r = self.relationship
        return 1 - math.exp(-(r["mensajes"] / 400 + r["sesiones"] / 20))

    def familiarity_level(self) -> tuple[str, str]:
        f = self.familiarity()
        for limit, label, guidance in FAMILIARITY_LEVELS:
            if f < limit:
                return label, guidance
        return FAMILIARITY_LEVELS[-1][1], FAMILIARITY_LEVELS[-1][2]

    def last_session_at(self) -> datetime | None:
        value = self.relationship.get("ultima_sesion")
        return parse_dt(value) if value else None

    # ------------------------------------------------------------------
    # Evolución
    # ------------------------------------------------------------------

    def adjust_trait(self, name: str, delta: float) -> bool:
        spec = self.state["rasgos"].get(name)
        if spec is None:
            return False
        lo, hi = self._window(spec["base"])
        spec["valor"] = round(_clamp(spec["valor"] + _clamp(delta, -MAX_STEP, MAX_STEP), lo, hi), 3)
        return True

    def adjust_humor(self, style: str, delta: float) -> None:
        style = style.strip().lower()
        if not style:
            return
        styles = self.state["humor"]["estilos"]
        key = next((s for s in styles if normalize(s) == normalize(style)), style)
        current = styles.get(key, 0.3)
        styles[key] = round(_clamp(current + _clamp(delta, -MAX_STEP, MAX_STEP), 0.05, 1.0), 3)

    def add_inside_joke(self, joke: str, origin: str = "") -> bool:
        jokes = self.state["humor"]["chistes_internos"]
        if not joke.strip() or any(normalize(j["chiste"]) == normalize(joke) for j in jokes):
            return False
        jokes.append({"chiste": joke.strip(), "origen": origin.strip(), "fecha": iso(now())})
        del jokes[:-MAX_JOKES]
        return True

    def add_humor_note(self, note: str) -> None:
        if note.strip():
            notes = self.state["humor"]["notas"]
            notes.append(note.strip())
            del notes[:-MAX_HUMOR_NOTES]

    def add_taste(self, topic: str, opinion: str) -> None:
        """Nuevo gusto u opinión propia de Ali (reemplaza la anterior del mismo tema)."""
        if not topic.strip() or not opinion.strip():
            return
        tastes = [g for g in self.state["gustos"] if normalize(g["tema"]) != normalize(topic)]
        tastes.append({"tema": topic.strip(), "opinion": opinion.strip(), "fecha": iso(now())})
        self.state["gustos"] = tastes[-MAX_TASTES:]

    def set_mood(self, mood: str) -> None:
        if mood.strip():
            self.state["animo"] = {"estado": mood.strip(), "desde": iso(now())}

    def set_user_name(self, name: str) -> None:
        self.user["nombre"] = name.strip()

    def set_user_fact(self, key: str, value: str) -> None:
        key = key.strip().lower()
        if not key:
            return
        if value.strip():
            self.user["datos"][key] = value.strip()
        else:
            self.user["datos"].pop(key, None)

    def set_user_style(self, style: str) -> None:
        if style.strip():
            self.user["estilo_comunicacion"] = style.strip()

    def add_nickname(self, nickname: str) -> None:
        nicks = self.user["apodos"]
        if nickname.strip() and all(normalize(n) != normalize(nickname) for n in nicks):
            nicks.append(nickname.strip())
            del nicks[:-MAX_NICKNAMES]

    def add_relationship_note(self, note: str) -> None:
        if note.strip():
            notes = self.relationship["notas"]
            notes.append(f"{now():%Y-%m-%d}: {note.strip()}")
            del notes[:-MAX_REL_NOTES]

    def register_session_start(self) -> datetime | None:
        """Cuenta una sesión nueva. Devuelve cuándo fue la anterior."""
        previous = self.last_session_at()
        self.relationship["sesiones"] += 1
        self.relationship["ultima_sesion"] = iso(now())
        self.save()
        return previous

    def register_message(self) -> None:
        self.relationship["mensajes"] += 1
        self.relationship["ultima_sesion"] = iso(now())
