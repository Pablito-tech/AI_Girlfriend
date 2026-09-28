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


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
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

    # --- Telegram ---
    telegram_token: str | None = None      # token que te da @BotFather
    telegram_user_id: int | None = None    # tu ID de Telegram: sólo contigo habla Ali
    telegram_session_min: int = 30         # minutos sin mensajes para dar la conversación por terminada
    telegram_reminder_min: int = 30        # avisarte N minutos antes de un evento (0 = no avisar)
    telegram_morning: str = ""             # hora del mensaje de buenos días, ej. "08:30" (vacío = no)
    telegram_wait_s: float = 2.5           # espera a que termines de escribir antes de responder

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
            telegram_token=e("TELEGRAM_BOT_TOKEN") or None,
            telegram_user_id=_int(e("TELEGRAM_USER_ID"), 0) or None,
            telegram_session_min=_int(e("ALI_TELEGRAM_SESION_MIN"), cls.telegram_session_min),
            telegram_reminder_min=_int(e("ALI_TELEGRAM_AVISO_MIN"), cls.telegram_reminder_min),
            telegram_morning=(e("ALI_TELEGRAM_BUENOS_DIAS") or "").strip(),
            telegram_wait_s=_float(e("ALI_TELEGRAM_ESPERA"), cls.telegram_wait_s),
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def api_key_problem(self) -> str | None:
        """Detecta errores comunes al copiar la API key. Devuelve el problema o None."""
        key = self.api_key
        if not key:
            if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
                return None
            return ("No encontré ANTHROPIC_API_KEY. Copia .env.example a .env y pon ahí tu API key\n"
                    "  de https://platform.claude.com (sección API Keys).")
        if "..." in key or "…" in key:
            return ("Tu API key tiene '...': es la versión recortada que muestra la lista de keys.\n"
                    "  La key completa sólo se ve una vez, al crearla. Crea una nueva en\n"
                    "  platform.claude.com → API Keys → Create Key, y cópiala entera en tu .env.")
        if any(c.isspace() for c in key):
            return "Tu API key tiene espacios o saltos de línea en medio. Revisa que esté en una sola línea."
        if not key.startswith("sk-ant-"):
            return "Tu API key no empieza con 'sk-ant-'. Revisa que copiaste la key correcta."
        if len(key) < 60:
            return (f"Tu API key parece incompleta (sólo tiene {len(key)} caracteres; una completa\n"
                    "  tiene alrededor de 100). Crea una nueva y cópiala entera.")
        return None
