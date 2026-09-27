"""Habilidad de archivos: Ali lee, escribe y organiza archivos en su carpeta.

Por seguridad, Ali sólo puede tocar lo que está dentro de `ALI_FILES_DIR`
(por defecto `data/archivos`). Cualquier ruta que intente salir de ahí
(`..`, rutas absolutas, enlaces simbólicos) se rechaza.
"""

from __future__ import annotations

from pathlib import Path

from .base import Param, Skill, ToolError, tool

MAX_READ_CHARS = 60_000
MAX_WRITE_CHARS = 200_000
TEXT_SUFFIXES = {
    ".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css", ".xml", ".yaml", ".yml",
    ".toml", ".ini", ".log", ".srt", ".vtt", ".tex", ".sql", ".sh", ".java", ".c", ".cpp", ".rs", ".go",
}


def safe_path(root: Path, relative: str) -> Path:
    """Resuelve `relative` dentro de `root` o lanza ToolError si intenta escapar."""
    root = root.resolve()
    target = (root / relative.strip().lstrip("/\\")).resolve()
    if target != root and not target.is_relative_to(root):
        raise ToolError("Esa ruta está fuera de tu carpeta de archivos.")
    return target


class ArchivosSkill(Skill):
    name = "archivos"
    description = "Leer y escribir archivos en la carpeta de Ali"

    @property
    def root(self) -> Path:
        self.ctx.files_dir.mkdir(parents=True, exist_ok=True)
        return self.ctx.files_dir.resolve()

    def prompt_guidance(self) -> str:
        return f"""\
## Archivos
Tienes una carpeta de archivos compartida con tu pareja ({self.root}). Puedes listar, leer, \
buscar y escribir archivos ahí (notas, listas, resúmenes, código...). Las rutas son relativas \
a esa carpeta. Antes de sobrescribir algo importante, confírmalo."""

    def _rel(self, path: Path) -> str:
        return str(path.relative_to(self.root)) or "."

    @tool(
        "Lista archivos y carpetas.",
        carpeta=Param("string", "Subcarpeta relativa (vacío = raíz).", required=False),
    )
    def listar_archivos(self, carpeta: str = ""):
        folder = safe_path(self.root, carpeta or "")
        if not folder.is_dir():
            raise ToolError("Esa carpeta no existe.")
        entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        if not entries:
            return "La carpeta está vacía."
        lines = []
        for p in entries[:200]:
            if p.is_dir():
                lines.append(f"📁 {self._rel(p)}/")
            else:
                lines.append(f"📄 {self._rel(p)} ({p.stat().st_size:,} bytes)")
        return "\n".join(lines)

    @tool("Lee un archivo de texto.", ruta=Param("string", "Ruta relativa del archivo."))
    def leer_archivo(self, ruta: str):
        path = safe_path(self.root, ruta)
        if not path.is_file():
            raise ToolError("Ese archivo no existe.")
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            raise ToolError("Es una imagen: usa `ver_imagen` para verla.")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolError("No es un archivo de texto legible.")
        if len(text) > MAX_READ_CHARS:
            return text[:MAX_READ_CHARS] + f"\n\n[... archivo recortado: {len(text):,} caracteres en total]"
        return text or "(archivo vacío)"

    @tool(
        "Crea o modifica un archivo de texto.",
        ruta=Param("string", "Ruta relativa del archivo (se crean las carpetas necesarias)."),
        contenido=Param("string", "Texto a escribir."),
        agregar=Param("boolean", "true = agregar al final; false = sobrescribir (por defecto).", required=False),
    )
    def escribir_archivo(self, ruta: str, contenido: str, agregar: bool = False):
        if len(contenido) > MAX_WRITE_CHARS:
            raise ToolError("El contenido es demasiado largo.")
        path = safe_path(self.root, ruta)
        if path == self.root or path.is_dir():
            raise ToolError("La ruta debe ser un archivo, no una carpeta.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a" if agregar else "w", encoding="utf-8") as f:
            f.write(contenido)
        return f"{'Agregado a' if agregar else 'Guardado'} {self._rel(path)}."

    @tool(
        "Busca un texto dentro de los archivos de texto.",
        texto=Param("string", "Texto a buscar (no distingue mayúsculas)."),
    )
    def buscar_en_archivos(self, texto: str):
        needle = texto.lower()
        hits = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if needle in line.lower():
                        hits.append(f"{self._rel(path)}:{n}: {line.strip()[:200]}")
                        if len(hits) >= 40:
                            return "\n".join(hits) + "\n[... hay más resultados]"
            except (UnicodeDecodeError, OSError):
                continue
        return "\n".join(hits) if hits else "No encontré ese texto en tus archivos."
