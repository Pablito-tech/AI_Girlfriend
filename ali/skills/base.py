"""Sistema de habilidades (plugins) de Ali.

Una habilidad es una clase que agrupa herramientas que Claude puede usar.
Para crear una nueva sólo necesitas:

    from ali.skills.base import Skill, tool, Param

    class ClimaSkill(Skill):
        name = "clima"
        description = "Consultar el clima"

        @tool("Da el clima actual de una ciudad", ciudad=Param("string", "Nombre de la ciudad"))
        def ver_clima(self, ciudad: str) -> str:
            return f"En {ciudad} hace sol"

Guárdala en `ali/skills/clima.py` y agrega "clima" a ALI_SKILLS en tu .env.
Además de herramientas, una habilidad puede aportar:

- `setup()`: crear sus tablas en la base de datos.
- `prompt_guidance()`: instrucciones fijas para el prompt de sistema.
- `session_briefing()`: algo que Ali debe saber al iniciar cada sesión.
- `turn_context(texto)`: algo que Ali debe saber antes de responder un mensaje.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Union

if TYPE_CHECKING:
    from ..config import Config
    from ..memory import MemoryStore
    from ..personality import Personality
    from ..storage import Database

# Lo que devuelve una herramienta: texto, o una lista de bloques de contenido
# (por ejemplo imágenes) tal como los acepta la API de Claude.
ToolOutput = Union[str, list[dict[str, Any]]]


class ToolError(Exception):
    """Error "esperado" de una herramienta: se le explica a Ali y ella decide qué hacer."""


@dataclass
class Param:
    type: str                      # string | integer | number | boolean | array
    description: str
    required: bool = True
    enum: list[Any] | None = None
    items: str = "string"          # tipo de los elementos si type == "array"

    def schema(self) -> dict[str, Any]:
        s: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum:
            s["enum"] = list(self.enum)
        if self.type == "array":
            s["items"] = {"type": self.items}
        return s


def tool(description: str, **params: Param) -> Callable:
    """Marca un método de una Skill como herramienta disponible para Claude."""

    def decorator(fn: Callable) -> Callable:
        fn._tool_spec = (description, params)
        return fn

    return decorator


_PY_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
}


def _type_ok(value: Any, type_name: str) -> bool:
    if type_name in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _PY_TYPES.get(type_name, (object,)))


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, Param]
    handler: Callable[..., ToolOutput]
    skill: str = ""

    def api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {k: p.schema() for k, p in self.params.items()},
                "required": [k for k, p in self.params.items() if p.required],
            },
            # Los argumentos llegan mientras se generan (streaming); por eso los
            # validamos nosotros en `validate` antes de ejecutar nada.
            "eager_input_streaming": True,
        }

    def validate(self, args: Any) -> tuple[dict[str, Any] | None, str | None]:
        """Revisa los argumentos contra el esquema. Devuelve (args_limpios, error)."""
        if not isinstance(args, dict):
            return None, "Los argumentos deben ser un objeto JSON."
        clean: dict[str, Any] = {}
        for key, p in self.params.items():
            if key not in args or args[key] is None:
                if p.required:
                    return None, f"Falta el argumento obligatorio '{key}'."
                continue
            value = args[key]
            if p.type == "number" and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if not _type_ok(value, p.type):
                return None, f"El argumento '{key}' debe ser de tipo {p.type}."
            if p.type == "array" and not all(_type_ok(v, p.items) for v in value):
                return None, f"Los elementos de '{key}' deben ser de tipo {p.items}."
            if p.enum and value not in p.enum:
                return None, f"'{key}' debe ser uno de: {', '.join(map(str, p.enum))}."
            clean[key] = value
        return clean, None


@dataclass
class SkillContext:
    """Todo lo que una habilidad puede necesitar de Ali."""

    config: "Config"
    db: "Database"
    memory: "MemoryStore"
    personality: "Personality"
    session: dict[str, Any] = field(default_factory=dict)  # datos de la sesión actual (id, etc.)

    @property
    def files_dir(self) -> Path:
        return self.config.files_dir


class Skill:
    name: str = ""
    description: str = ""

    def __init__(self, ctx: SkillContext):
        self.ctx = ctx
        self.setup()

    # --- ganchos opcionales ----------------------------------------------

    def setup(self) -> None:
        """Crea tablas u otros recursos que la habilidad necesite."""

    def prompt_guidance(self) -> str:
        return ""

    def session_briefing(self) -> str:
        return ""

    def turn_context(self, user_text: str) -> str:
        return ""

    # --- herramientas -----------------------------------------------------

    def tools(self) -> list[Tool]:
        found = []
        for attr_name, fn in inspect.getmembers(type(self), predicate=inspect.isfunction):
            spec = getattr(fn, "_tool_spec", None)
            if spec:
                description, params = spec
                found.append(Tool(attr_name, description, params, getattr(self, attr_name), self.name))
        return found


class SkillSet:
    """Conjunto de habilidades activas; traduce entre Claude y los plugins."""

    def __init__(self, skills: list[Skill]):
        self.skills = skills
        self._tools: dict[str, Tool] = {}
        for s in skills:
            for t in s.tools():
                if t.name in self._tools:
                    raise ValueError(f"Herramienta duplicada: {t.name}")
                self._tools[t.name] = t

    @classmethod
    def load(cls, names: list[str], ctx: SkillContext) -> "SkillSet":
        skills = []
        for name in names:
            module_path = name if "." in name else f"{__package__}.{name}"
            module = importlib.import_module(module_path)
            classes = [
                obj for _, obj in inspect.getmembers(module, inspect.isclass)
                if issubclass(obj, Skill) and obj is not Skill and obj.__module__ == module.__name__
            ]
            if not classes:
                raise ValueError(f"El módulo {module_path} no define ninguna Skill")
            skills.extend(c(ctx) for c in classes)
        return cls(skills)

    def api_tools(self) -> list[dict[str, Any]]:
        # Orden estable (por habilidad y nombre) para aprovechar la caché de prompts.
        return [t.api_schema() for t in self._tools.values()]

    def tool_names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def execute(self, name: str, args: Any) -> tuple[ToolOutput, bool]:
        """Ejecuta una herramienta. Devuelve (salida, es_error)."""
        t = self._tools.get(name)
        if t is None:
            return f"No existe la herramienta '{name}'.", True
        clean, error = t.validate(args)
        if error:
            return f"Argumentos inválidos: {error}", True
        try:
            return t.handler(**clean), False
        except ToolError as e:
            return str(e), True
        except Exception as e:  # una herramienta rota no debe tumbar la conversación
            return f"La herramienta falló: {type(e).__name__}: {e}", True

    def guidance(self) -> str:
        parts = [s.prompt_guidance().strip() for s in self.skills]
        return "\n\n".join(p for p in parts if p)

    def briefing(self) -> str:
        parts = []
        for s in self.skills:
            try:
                text = s.session_briefing().strip()
            except Exception:
                text = ""
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    def turn_context(self, user_text: str) -> str:
        parts = []
        for s in self.skills:
            try:
                text = s.turn_context(user_text).strip()
            except Exception:
                text = ""
            if text:
                parts.append(text)
        return "\n\n".join(parts)
