"""Utilidades compartidas: reloj, fechas en español y procesamiento de texto.

Todo lo que tiene que ver con "¿qué hora es?" pasa por `now()`, así las
pruebas pueden congelar el tiempo con `set_clock()`.
"""

from __future__ import annotations

import base64
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

# --------------------------------------------------------------------------
# Reloj
# --------------------------------------------------------------------------

_clock: Callable[[], datetime] = lambda: datetime.now().replace(microsecond=0)


def now() -> datetime:
    """Hora local actual (sin zona horaria, precisión de segundos)."""
    return _clock()


def set_clock(clock: Callable[[], datetime] | None) -> None:
    """Reemplaza el reloj (útil en pruebas). `None` restaura el real."""
    global _clock
    _clock = clock or (lambda: datetime.now().replace(microsecond=0))


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def parse_dt(value: str) -> datetime:
    """Acepta 'YYYY-MM-DD', 'YYYY-MM-DDTHH:MM' o 'YYYY-MM-DD HH:MM[:SS]'."""
    value = value.strip().replace(" ", "T")
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=None, microsecond=0)


# --------------------------------------------------------------------------
# Fechas en español (sin depender del locale del sistema)
# --------------------------------------------------------------------------

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def fecha_larga(dt: datetime, con_hora: bool = True) -> str:
    texto = f"{DIAS[dt.weekday()]} {dt.day} de {MESES[dt.month - 1]} de {dt.year}"
    if con_hora:
        texto += f", {dt:%H:%M}"
    return texto


def momento_del_dia(dt: datetime) -> str:
    h = dt.hour
    if 5 <= h < 12:
        return "mañana"
    if 12 <= h < 19:
        return "tarde"
    if 19 <= h < 24:
        return "noche"
    return "madrugada"


def hace_cuanto(pasado: datetime, ahora: datetime | None = None) -> str:
    """'hace 5 minutos', 'hace 3 días', 'hace 2 meses'..."""
    ahora = ahora or now()
    seg = max(0, (ahora - pasado).total_seconds())
    minutos, horas, dias = seg / 60, seg / 3600, seg / 86400
    if minutos < 1:
        return "hace un momento"
    if minutos < 60:
        n = int(minutos)
        return f"hace {n} minuto{'s' if n != 1 else ''}"
    if horas < 24:
        n = int(horas)
        return f"hace {n} hora{'s' if n != 1 else ''}"
    if dias < 30:
        n = int(dias)
        return "ayer" if n == 1 else f"hace {n} días"
    if dias < 365:
        n = int(dias // 30)
        return f"hace {n} mes{'es' if n != 1 else ''}"
    n = int(dias // 365)
    return f"hace {n} año{'s' if n != 1 else ''}"


# --------------------------------------------------------------------------
# Texto: normalización, tokens y relevancia (BM25)
# --------------------------------------------------------------------------

STOPWORDS = set(
    """
    a al algo algun alguna algunas alguno algunos ante antes aqui asi aun aunque bien cada casi
    como con contra cual cuales cuando de del desde donde dos el ella ellas ello ellos en entre
    era eran eres es esa esas ese eso esos esta estaba estaban estamos estan estar estas este
    esto estos estoy fue fueron fui ha habia han has hasta hay he la las le les lo los mas me mi
    mis mucho muy nada ni no nos nosotros o os otra otro para pero poco por porque que quien
    se sea ser si sin sobre solo son su sus tambien tan te tener tengo ti tiene tienen todo
    todos tu tus un una unas uno unos usted ustedes y ya yo
    the and or of to in on at is are was were be been it this that with for as i you he she we
    they my your our me him her them do does did have has had not no so if but just
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9ñ]+")


def normalize(text: str) -> str:
    """Minúsculas y sin acentos (conserva la ñ)."""
    text = text.lower().replace("ñ", "\x00")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.replace("\x00", "ñ")


def _stem(word: str) -> str:
    """Raíz ligera para español/inglés: quita plural, vocal final y trunca.

    Es deliberadamente simple: 'animes' y 'anime' -> 'anim'; 'perras' y
    'perro' -> 'perr'; 'ciudades' -> 'ciudad'. Si algún día quieres algo más
    fino, cambia sólo esta función.
    """
    if len(word) > 3 and word.endswith("s"):
        word = word[:-1]
    if len(word) > 3 and word[-1] in "aeo":
        word = word[:-1]
    return word[:7]


def tokenize(text: str) -> list[str]:
    return [_stem(w) for w in _WORD_RE.findall(normalize(text)) if w not in STOPWORDS and len(w) > 1]


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def bm25_scores(
    query_tokens: Sequence[str],
    docs_tokens: Sequence[Sequence[str]],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """Puntaje BM25 de cada documento para la consulta.

    BM25 premia que las palabras de la consulta aparezcan en el recuerdo,
    sobre todo si son palabras raras (poco comunes entre todos los recuerdos).
    """
    n = len(docs_tokens)
    if n == 0 or not query_tokens:
        return [0.0] * n
    avgdl = sum(len(d) for d in docs_tokens) / n or 1.0
    df: Counter[str] = Counter()
    for d in docs_tokens:
        df.update(set(d))
    q_terms = set(query_tokens)
    scores = []
    for d in docs_tokens:
        tf = Counter(d)
        dl = len(d) or 1
        s = 0.0
        for term in q_terms:
            f = tf.get(term, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scores.append(s)
    return scores


def truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------
# Imágenes
# --------------------------------------------------------------------------

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def image_block(path: Path) -> dict:
    """Convierte una imagen en un bloque de contenido para Claude (ValueError si no se puede)."""
    media_type = IMAGE_TYPES.get(Path(path).suffix.lower())
    if not media_type:
        raise ValueError(f"Formato de imagen no soportado ({Path(path).suffix}). Usa png, jpg, gif o webp.")
    data = Path(path).read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("La imagen pesa más de 5 MB.")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode()},
    }
