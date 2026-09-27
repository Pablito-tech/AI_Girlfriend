"""Configuración de Ali, leída de variables de entorno (o del archivo .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ALL_SKILLS = ["memoria", "personalidad", "agenda", "archivos", "multimedia"]


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "si", "sí", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


@dataclass
class Config:
    # --- Claude ---
    api_key: str | None = None
    model: str = "claude-opus-5"
    effort: str = "medium"             # esfuerzo al conversar: low | medium | high | xhigh | max
    reflection_effort: str = "high"    # esfuerzo al consolidar recuerdos
    max_tokens: int = 64000
    web_search: bool = True            # permite a Ali buscar en internet para opinar/informarse
    fallbacks: bool = True             # reintento automático en otro modelo si hay un rechazo

    # --- Rutas ---
    data_dir: Path = PROJECT_ROOT / "data"
    files_dir: Path = PROJECT_ROOT / "data" / "archivos"
    persona_file: Path = PROJECT_ROOT / "config" / "persona.toml"

    # --- Comportamiento ---
    greeting: bool = True              # Ali saluda al iniciar
    reflect_every: int = 30            # consolidar recuerdos cada N mensajes tuyos (0 = sólo al salir)
    history_messages: int = 20         # mensajes de sesiones anteriores que se cargan al iniciar
    memories_per_turn: int = 5         # recuerdos que "le vienen a la mente" en cada mensaje
    max_session_messages: int = 160    # si la sesión crece más, se recorta lo más viejo
    skills: list[str] = field(default_factory=lambda: list(ALL_SKILLS))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ali.db"

    @property
    def personality_path(self) -> Path:
        return self.data_dir / "personalidad.json"

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Config":
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file or PROJECT_ROOT / ".env")
        except ImportError:  # python-dotenv es opcional
            pass

        e = os.environ.get
        data_dir = Path(e("ALI_DATA_DIR") or PROJECT_ROOT / "data").expanduser()
        skills_env = e("ALI_SKILLS")
        return cls(
            api_key=e("ANTHROPIC_API_KEY") or None,
            model=e("ALI_MODEL") or cls.model,
            effort=e("ALI_EFFORT") or cls.effort,
            reflection_effort=e("ALI_REFLECTION_EFFORT") or cls.reflection_effort,
            max_tokens=_int(e("ALI_MAX_TOKENS"), cls.max_tokens),
            web_search=_bool(e("ALI_WEB_SEARCH"), True),
            fallbacks=_bool(e("ALI_FALLBACKS"), True),
            data_dir=data_dir,
            files_dir=Path(e("ALI_FILES_DIR") or data_dir / "archivos").expanduser(),
            persona_file=Path(e("ALI_PERSONA_FILE") or PROJECT_ROOT / "config" / "persona.toml").expanduser(),
            greeting=_bool(e("ALI_SALUDO"), True),
            reflect_every=_int(e("ALI_REFLECT_EVERY"), cls.reflect_every),
            history_messages=_int(e("ALI_HISTORY_MESSAGES"), cls.history_messages),
            memories_per_turn=_int(e("ALI_MEMORIES_PER_TURN"), cls.memories_per_turn),
            skills=[s.strip() for s in skills_env.split(",") if s.strip()] if skills_env else list(ALL_SKILLS),
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
