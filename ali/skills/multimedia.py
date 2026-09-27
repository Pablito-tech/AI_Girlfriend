"""Habilidad multimedia: anime, series, libros... y "ver" cosas juntos.

- Lleva una lista de obras (qué están viendo/leyendo, progreso, opiniones).
- `ver_imagen`: Ali mira una imagen o captura de pantalla.
- `ver_fragmento`: Ali "ve" un fragmento de un video: se extraen fotogramas
  con ffmpeg (si está instalado) y se leen los subtítulos .srt/.vtt que
  tengan el mismo nombre que el video. Es la base para ver anime juntos.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import utils
from ..utils import iso, normalize, now
from .archivos import safe_path
from .base import Param, Skill, ToolError, tool

MAX_FRAMES = 8
KINDS = ["anime", "serie", "pelicula", "manga", "libro", "videojuego", "otro"]
STATUSES = ["pendiente", "viendo", "terminado", "en pausa", "abandonado"]

_TIME_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def image_block(path: Path) -> dict:
    try:
        return utils.image_block(path)
    except ValueError as e:
        raise ToolError(str(e))


def _seconds(match: re.Match) -> float:
    h, m, s, ms = (int(g) for g in match.groups())
    return h * 3600 + m * 60 + s + ms / 1000


def read_subtitles(path: Path, start: float = 0, end: float | None = None) -> list[str]:
    """Líneas de subtítulos (.srt o .vtt) entre `start` y `end` segundos."""
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    lines = []
    for block in re.split(r"\n\s*\n", text):
        times = list(_TIME_RE.finditer(block))
        if len(times) < 2:
            continue
        t0, t1 = _seconds(times[0]), _seconds(times[1])
        if t1 < start or (end is not None and t0 > end):
            continue
        rest = block[times[1].end():]
        body = rest.split("\n", 1)[1] if "\n" in rest else ""
        body = re.sub(r"<[^>]+>|\{[^}]+\}", "", body).strip().replace("\n", " ")
        if body:
            lines.append(f"[{int(t0 // 60):02d}:{int(t0 % 60):02d}] {body}")
    return lines


class MultimediaSkill(Skill):
    name = "multimedia"
    description = "Anime, series, libros y ver cosas juntos"

    def setup(self) -> None:
        self.ctx.db.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS media (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT NOT NULL,
                kind         TEXT NOT NULL,
                status       TEXT NOT NULL,
                progress     TEXT NOT NULL DEFAULT '',
                user_rating  INTEGER,
                ali_opinion  TEXT NOT NULL DEFAULT '',
                notes        TEXT NOT NULL DEFAULT '',
                updated_at   TEXT NOT NULL
            );
            """
        )

    @property
    def root(self) -> Path:
        self.ctx.files_dir.mkdir(parents=True, exist_ok=True)
        return self.ctx.files_dir.resolve()

    def prompt_guidance(self) -> str:
        return """\
## Anime, series, libros y más
Llevas la lista de lo que tu pareja ve, lee o juega (`registrar_obra`, `actualizar_obra`, \
`ver_obras`). Opina de verdad sobre lo que ven: tienes gustos propios. Si te comparte una \
imagen o captura, puedes verla con `ver_imagen`; si te pide ver un fragmento de un video \
de su carpeta, usa `ver_fragmento` (fotogramas + subtítulos) y reacciona como si lo \
estuvieran viendo juntos. Evita spoilers de lo que aún no ha visto."""

    # --- lista de obras -------------------------------------------------

    def _find(self, titulo: str):
        rows = self.ctx.db.query("SELECT * FROM media ORDER BY updated_at DESC")
        key = normalize(titulo)
        exact = [r for r in rows if normalize(r["title"]) == key]
        partial = [r for r in rows if key in normalize(r["title"])]
        match = exact or partial
        if not match:
            raise ToolError(f"No tienes '{titulo}' en la lista. Usa registrar_obra primero.")
        return match[0]

    @staticmethod
    def _fmt(r) -> str:
        parts = [f"[{r['id']}] {r['title']} ({r['kind']}) — {r['status']}"]
        if r["progress"]:
            parts.append(f"progreso: {r['progress']}")
        if r["user_rating"] is not None:
            parts.append(f"su calificación: {r['user_rating']}/10")
        if r["ali_opinion"]:
            parts.append(f"tu opinión: {r['ali_opinion']}")
        if r["notes"]:
            parts.append(f"notas: {r['notes']}")
        return " | ".join(parts)

    @tool(
        "Agrega una obra (anime, serie, película, libro...) a la lista.",
        titulo=Param("string", "Título."),
        tipo=Param("string", "Tipo de obra.", enum=KINDS),
        estado=Param("string", "Estado.", enum=STATUSES),
        progreso=Param("string", "Progreso, ej. 'ep 5/12', 'cap 30'.", required=False),
        notas=Param("string", "Notas.", required=False),
    )
    def registrar_obra(self, titulo: str, tipo: str, estado: str, progreso: str = "", notas: str = ""):
        media_id = self.ctx.db.execute(
            "INSERT INTO media (title, kind, status, progress, notes, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (titulo.strip(), tipo, estado, progreso or "", notas or "", iso(now())),
        ).lastrowid
        row = self.ctx.db.query_one("SELECT * FROM media WHERE id = ?", (media_id,))
        return "Agregado: " + self._fmt(row)

    @tool(
        "Actualiza una obra de la lista (sólo los campos que indiques).",
        titulo=Param("string", "Título (o parte del título) de la obra."),
        estado=Param("string", "Nuevo estado.", required=False, enum=STATUSES),
        progreso=Param("string", "Nuevo progreso.", required=False),
        calificacion_usuario=Param("integer", "Calificación que le da tu pareja, 1-10.", required=False),
        opinion_ali=Param("string", "Tu opinión sobre la obra.", required=False),
        notas=Param("string", "Notas.", required=False),
    )
    def actualizar_obra(self, titulo: str, estado=None, progreso=None, calificacion_usuario=None,
                        opinion_ali=None, notas=None):
        r = self._find(titulo)
        self.ctx.db.execute(
            """UPDATE media SET status = ?, progress = ?, user_rating = ?, ali_opinion = ?, notes = ?,
               updated_at = ? WHERE id = ?""",
            (
                estado or r["status"],
                r["progress"] if progreso is None else progreso,
                r["user_rating"] if calificacion_usuario is None else max(1, min(10, calificacion_usuario)),
                r["ali_opinion"] if opinion_ali is None else opinion_ali,
                r["notes"] if notas is None else notas,
                iso(now()),
                r["id"],
            ),
        )
        if opinion_ali:
            self.ctx.personality.add_taste(r["title"], opinion_ali)
            self.ctx.personality.save()
        return "Actualizado: " + self._fmt(self.ctx.db.query_one("SELECT * FROM media WHERE id = ?", (r["id"],)))

    @tool(
        "Muestra la lista de obras.",
        estado=Param("string", "Filtrar por estado.", required=False, enum=STATUSES),
    )
    def ver_obras(self, estado=None):
        if estado:
            rows = self.ctx.db.query("SELECT * FROM media WHERE status = ? ORDER BY updated_at DESC", (estado,))
        else:
            rows = self.ctx.db.query("SELECT * FROM media ORDER BY updated_at DESC")
        return "\n".join(self._fmt(r) for r in rows) if rows else "La lista está vacía."

    # --- ver cosas ------------------------------------------------------

    @tool("Mira una imagen de la carpeta de archivos.", ruta=Param("string", "Ruta relativa de la imagen."))
    def ver_imagen(self, ruta: str):
        path = safe_path(self.root, ruta)
        if not path.is_file():
            raise ToolError("Esa imagen no existe.")
        return [image_block(path), {"type": "text", "text": f"Imagen: {path.name}"}]

    @tool(
        "Mira un fragmento de un video de la carpeta de archivos (fotogramas + subtítulos si existen).",
        ruta=Param("string", "Ruta relativa del video."),
        inicio_seg=Param("number", "Segundo en que empieza el fragmento."),
        duracion_seg=Param("number", "Duración del fragmento en segundos (por defecto 60).", required=False),
        fotogramas=Param("integer", f"Cuántos fotogramas mirar (1-{MAX_FRAMES}, por defecto 4).", required=False),
    )
    def ver_fragmento(self, ruta: str, inicio_seg: float, duracion_seg=None, fotogramas=None):
        video = safe_path(self.root, ruta)
        if not video.is_file():
            raise ToolError("Ese video no existe.")
        duration = max(1.0, min(600.0, duracion_seg or 60.0))
        n = max(1, min(MAX_FRAMES, fotogramas or 4))
        start = max(0.0, inicio_seg)
        blocks: list[dict] = []

        for ext in (".srt", ".vtt"):
            subs = video.with_suffix(ext)
            if subs.is_file():
                lines = read_subtitles(subs, start, start + duration)
                if lines:
                    blocks.append({"type": "text", "text": "Subtítulos del fragmento:\n" + "\n".join(lines)})
                break

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            if not blocks:
                raise ToolError("Para ver video hace falta instalar ffmpeg (y no hay subtítulos junto al video).")
            blocks.append({"type": "text", "text": "(No se pudieron extraer imágenes: ffmpeg no está instalado.)"})
            return blocks

        with tempfile.TemporaryDirectory() as tmp:
            for i in range(n):
                t = start + duration * (i + 0.5) / n
                out = Path(tmp) / f"frame_{i:02d}.jpg"
                subprocess.run(
                    [ffmpeg, "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
                     "-frames:v", "1", "-vf", "scale=768:-2", "-q:v", "5", str(out)],
                    check=False, timeout=60,
                )
                if out.is_file():
                    blocks.append({"type": "text", "text": f"Fotograma en {int(t // 60):02d}:{int(t % 60):02d}"})
                    blocks.append(image_block(out))
        if not any(b["type"] == "image" for b in blocks):
            raise ToolError("No se pudieron extraer fotogramas de ese video.")
        return blocks

    # --- contexto -------------------------------------------------------

    def session_briefing(self) -> str:
        rows = self.ctx.db.query("SELECT * FROM media WHERE status = 'viendo' ORDER BY updated_at DESC LIMIT 5")
        if not rows:
            return ""
        return "Lo que tu pareja está viendo/leyendo ahora:\n" + "\n".join(f"- {self._fmt(r)}" for r in rows)
