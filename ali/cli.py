"""Interfaz de terminal para hablar con Ali."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from .brain import Event
from .config import Config
from .core import Ali, friendly_error
from .utils import hace_cuanto, parse_dt

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
            if args:
                results = ali.memory.search(" ".join(args), limit=15, min_score=0.0, touch=False)
                mems = [r.memory for r in results]
            else:
                mems = ali.memory.recent(15)
            print("\n".join(m.render() for m in mems) or "Aún no hay recuerdos.")
            print(self.s.dim(f"({ali.memory.count()} recuerdos en total)"))
        elif cmd == "/olvidar":
            if args and args[0].lstrip("#").isdigit():
                ok = ali.memory.delete(int(args[0].lstrip("#")))
                print("Olvidado." if ok else "No existe ese recuerdo.")
            else:
                print("Uso: /olvidar <id>")
        elif cmd == "/agenda":
            print(self._run_tool("ver_agenda", {}))
        elif cmd == "/obras":
            print(self._run_tool("ver_obras", {}))
        elif cmd == "/perfil":
            u = ali.personality.user
            print(f"Nombre: {u['nombre'] or '(desconocido)'}")
            if u["apodos"]:
                print("Apodos: " + ", ".join(u["apodos"]))
            for k, v in sorted(u["datos"].items()):
                print(f"- {k}: {v}")
            if u["estilo_comunicacion"]:
                print(f"Tu estilo: {u['estilo_comunicacion']}")
        elif cmd == "/ali":
            self._show_personality()
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
            try:
                r = ali.reflect()
                print(self.s.dim(f"Listo: {len(r.recuerdos)} recuerdos consolidados." if r else "No había nada nuevo."))
            except Exception as e:
                print(self.s.err(friendly_error(e, ali.config.model)))
        elif cmd == "/exportar":
            print(f"Exportado en: {ali.export()}")
        else:
            print("Comando desconocido. Escribe /ayuda.")
        print()
        return True

    def _run_tool(self, name: str, args: dict) -> str:
        if not self.ali.skills.get(name):
            return "Esa habilidad no está activada."
        output, _ = self.ali.skills.execute(name, args)
        return output if isinstance(output, str) else str(output)

    def _show_personality(self) -> None:
        p = self.ali.personality
        label, _ = p.familiarity_level()
        rel = p.relationship
        print(f"{p.name} — confianza: {label} ({p.familiarity():.2f})")
        sesiones, mensajes = rel["sesiones"], rel["mensajes"]
        print(f"Se conocieron {hace_cuanto(parse_dt(rel['conocidos_desde']))} · "
              f"{sesiones} conversaci{'ón' if sesiones == 1 else 'ones'} · "
              f"{mensajes} mensaje{'' if mensajes == 1 else 's'}")
        print(f"Ánimo: {p.state['animo']['estado']}")
        print("Rasgos:")
        for name, v, desc in p.traits():
            bar = "█" * round(v * 10) + "░" * (10 - round(v * 10))
            print(f"  {name:<11} {bar} {v:.2f}  {desc}")
        print("Humor:")
        for style, w in p.humor_styles():
            print(f"  {w:.2f}  {style}")
        jokes = p.state["humor"]["chistes_internos"]
        if jokes:
            print("Bromas internas: " + " · ".join(j["chiste"] for j in jokes))
        print(f"Gustos propios: {len(p.state['gustos'])} (el más reciente: {p.state['gustos'][-1]['tema']})"
              if p.state["gustos"] else "Gustos propios: ninguno aún")

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
    if not config.api_key and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        print("⚠ No encontré ANTHROPIC_API_KEY. Copia .env.example a .env y pon ahí tu API key\n"
              "  de https://platform.claude.com (o expórtala como variable de entorno).\n")
    Chat(Ali(config)).run()


if __name__ == "__main__":
    main()
