"""Interfaz de terminal para hablar con Ali."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from . import commands
from .brain import Event
from .config import Config
from .core import Ali

TOOL_LABELS = {
    "guardar_recuerdo": "guardó un recuerdo",
    "buscar_recuerdos": "está tratando de recordar",
    "olvidar_recuerdo": "olvidó un recuerdo",
    "actualizar_dato_usuario": "anotó un dato tuyo",
    "formar_opinion": "se formó una opinión",
    "registrar_chiste_interno": "guardó una broma interna",
    "nuevo_apodo": "guardó un apodo para ti",
    "cambiar_animo": "cambió de ánimo",
    "agregar_evento": "anotó algo en tu agenda",
    "ver_agenda": "revisó tu agenda",
    "completar_evento": "marcó un pendiente como hecho",
    "editar_evento": "movió algo en tu agenda",
    "eliminar_evento": "borró algo de tu agenda",
    "listar_archivos": "revisó tus archivos",
    "leer_archivo": "leyó un archivo",
    "escribir_archivo": "escribió un archivo",
    "buscar_en_archivos": "buscó en tus archivos",
    "registrar_obra": "agregó algo a tu lista",
    "actualizar_obra": "actualizó tu lista",
    "ver_obras": "revisó tu lista",
    "ver_imagen": "está mirando la imagen",
    "ver_fragmento": "está viendo el video",
    "web_search": "está buscando en internet",
}

HELP = """\
Comandos:
  /recuerdos [texto]   ver recuerdos recientes o buscar en su memoria
  /olvidar <id>        borrar un recuerdo
  /agenda              ver tu agenda
  /obras               ver tu lista de anime/series/libros
  /perfil              lo que Ali sabe de ti
  /ali                 cómo es Ali hoy (rasgos, humor, ánimo, relación)
  /ver <imagen> [msg]  compartirle una imagen (ruta en tu computadora)
  /reflexionar         que consolide recuerdos ahora mismo
  /exportar            respaldo de todo en un JSON
  /salir               despedirse (también Ctrl+C o Ctrl+D)"""


class Style:
    def __init__(self) -> None:
        enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        if enabled and os.name == "nt":
            os.system("")  # activa colores ANSI en la consola de Windows
        self.enabled = enabled

    def _c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def ali(self, t: str) -> str:
        return self._c("1;35", t)

    def user(self, t: str) -> str:
        return self._c("1;36", t)

    def dim(self, t: str) -> str:
        return self._c("2", t)

    def err(self, t: str) -> str:
        return self._c("31", t)


class Chat:
    def __init__(self, ali: Ali):
        self.ali = ali
        self.s = Style()

    # --- mostrar respuestas ----------------------------------------------

    def show(self, events) -> None:
        prefix = self.s.ali(f"{self.ali.name}: ")
        at_line_start = True
        wrote = False
        for ev in events:  # type: Event
            if ev.kind == "text":
                if at_line_start:
                    print(prefix, end="")
                    prefix = " " * len(self.ali.name + ": ")
                print(ev.text, end="", flush=True)
                at_line_start = ev.text.endswith("\n")
                wrote = True
            elif ev.kind == "tool":
                if not at_line_start:
                    print()
                label = TOOL_LABELS.get(ev.text, f"usó {ev.text}")
                print(self.s.dim(f"  · {self.ali.name} {label}"))
                at_line_start = True
            elif ev.kind in ("notice", "retry", "refusal"):
                if not at_line_start:
                    print()
                print(self.s.dim(f"  ({ev.text})"))
                at_line_start = True
            elif ev.kind == "error":
                if not at_line_start:
                    print()
                print(self.s.err(f"  ⚠ {ev.text}"))
                at_line_start = True
        if wrote and not at_line_start:
            print()
        print()

    # --- comandos --------------------------------------------------------

    def command(self, line: str) -> bool:
        """Ejecuta un comando. Devuelve False si hay que salir."""
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]
        ali = self.ali

        if cmd in ("/salir", "/exit", "/quit"):
            return False
        if cmd in ("/ayuda", "/help"):
            print(HELP)
        elif cmd == "/recuerdos":
            print(commands.memories(ali, " ".join(args)))
        elif cmd == "/olvidar":
            print(commands.forget(ali, args[0] if args else ""))
        elif cmd == "/agenda":
            print(commands.run_tool(ali, "ver_agenda"))
        elif cmd == "/obras":
            print(commands.run_tool(ali, "ver_obras"))
        elif cmd == "/perfil":
            print(commands.profile(ali))
        elif cmd == "/ali":
            print(commands.personality(ali))
        elif cmd == "/ver":
            if not args:
                print("Uso: /ver <ruta_imagen> [mensaje]")
            else:
                path = Path(args[0]).expanduser()
                message = " ".join(args[1:]) or "Mira esto."
                self.show(ali.chat(message, images=[path]))
                return True
        elif cmd == "/reflexionar":
            print(self.s.dim(f"({ali.name} se queda pensando…)"))
            print(self.s.dim(commands.reflect(ali)))
        elif cmd == "/exportar":
            print(f"Exportado en: {ali.export()}")
        else:
            print("Comando desconocido. Escribe /ayuda.")
        print()
        return True

    # --- bucle principal -------------------------------------------------

    def run(self) -> None:
        ali = self.ali
        print(self.s.ali(f"♡ {ali.name}") + self.s.dim("  —  escribe /ayuda para ver comandos, /salir para despedirte\n"))

        if not ali.personality.user_name:
            try:
                name = input(self.s.dim(f"Antes de empezar, ¿cómo te llamas? (así te conocerá {ali.name}) ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if name:
                ali.personality.set_user_name(name)
                ali.personality.save()
            print()

        if ali.pending_reflections():
            print(self.s.dim(f"({ali.name} está ordenando sus recuerdos de la última vez…)"))
            ali.consolidate_pending()

        ali.start_session()
        if ali.config.greeting:
            self.show(ali.greet())

        user_label = self.s.user((ali.personality.user_name or "Tú") + ": ")
        try:
            while True:
                try:
                    line = input(user_label).strip()
                except EOFError:
                    print()
                    break
                if not line:
                    continue
                if line.startswith("/"):
                    if not self.command(line):
                        break
                    continue
                self.show(ali.chat(line))
        except KeyboardInterrupt:
            print()
        finally:
            print(self.s.dim(f"({ali.name} está guardando sus recuerdos…)"))
            try:
                ali.end_session()
            except KeyboardInterrupt:
                pass  # lo que no se consolidó queda pendiente para la próxima vez
            ali.close()
            print(self.s.ali(f"{ali.name}: ") + "¡Hasta pronto! ♡")


def main() -> None:
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    config = Config.from_env()
    problem = config.api_key_problem()
    if problem:
        print(f"⚠ {problem}")
        return
    Chat(Ali(config)).run()


if __name__ == "__main__":
    main()
