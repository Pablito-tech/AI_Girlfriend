import re

import pytest

from ali.memory import MemoryStore
from ali.personality import Personality
from ali.skills import SkillContext, SkillSet
from ali.skills.multimedia import read_subtitles
from ali.storage import Database


@pytest.fixture
def skills(config, clock):
    config.ensure_dirs()
    db = Database(config.db_path)
    ctx = SkillContext(config, db, MemoryStore(db), Personality.load(config.persona_file, config.personality_path))
    ctx.session["id"] = db.start_session()
    return SkillSet.load(config.skills, ctx)


def test_all_tools_have_valid_schemas(skills):
    tools = skills.api_tools()
    assert len(tools) >= 20
    for t in tools:
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", t["name"])
        assert t["input_schema"]["type"] == "object"
        assert set(t["input_schema"]["required"]) <= set(t["input_schema"]["properties"])
        assert t["eager_input_streaming"] is True
    assert skills.api_tools() == tools  # orden estable (caché de prompts)


def test_tool_inputs_are_validated(skills):
    out, err = skills.execute("guardar_recuerdo", {"contenido": "x", "tipo": "evento"})
    assert err and "importancia" in out
    out, err = skills.execute("guardar_recuerdo", {"contenido": "x", "tipo": "inventado", "importancia": 3})
    assert err and "tipo" in out
    out, err = skills.execute("guardar_recuerdo", {"contenido": "x", "tipo": "evento", "importancia": "alta"})
    assert err and "integer" in out
    out, err = skills.execute("no_existe", {})
    assert err


def test_memory_tools(skills):
    out, err = skills.execute(
        "guardar_recuerdo",
        {"contenido": "Su pareja tiene examen de física", "tipo": "evento", "importancia": 7,
         "etiquetas": ["escuela"], "fecha_seguimiento": "2026-10-02"},
    )
    assert not err and "#1" in out
    out, err = skills.execute("buscar_recuerdos", {"consulta": "examen"})
    assert not err and "física" in out
    out, err = skills.execute("actualizar_dato_usuario", {"clave": "cumpleaños", "valor": "3 de mayo"})
    assert not err
    assert skills.get("actualizar_dato_usuario").handler.__self__.ctx.personality.user["datos"]["cumpleaños"] == "3 de mayo"


def test_agenda_flow(skills, clock):
    out, err = skills.execute("agregar_evento", {"titulo": "Dentista", "fecha_hora": "2026-09-28T10:00"})
    assert not err and "mañana 10:00" in out
    out, _ = skills.execute("ver_agenda", {})
    assert "Dentista" in out
    assert "Dentista" in skills.briefing()
    skills.execute("completar_evento", {"id": 1})
    out, _ = skills.execute("ver_agenda", {})
    assert "No hay nada" in out
    out, err = skills.execute("agregar_evento", {"titulo": "x", "fecha_hora": "el martes"})
    assert err


def test_agenda_reminds_upcoming_events_once(skills, clock):
    skills.execute("agregar_evento", {"titulo": "Llamar a mamá", "fecha_hora": "2026-09-27T19:00"})
    assert "Llamar a mamá" in skills.turn_context("hola")
    assert "Llamar a mamá" not in skills.turn_context("hola otra vez")


def test_files_are_confined_to_their_folder(skills):
    out, err = skills.execute("escribir_archivo", {"ruta": "notas/lista.md", "contenido": "- ramen\n"})
    assert not err
    skills.execute("escribir_archivo", {"ruta": "notas/lista.md", "contenido": "- sushi\n", "agregar": True})
    out, err = skills.execute("leer_archivo", {"ruta": "notas/lista.md"})
    assert out == "- ramen\n- sushi\n"
    out, err = skills.execute("buscar_en_archivos", {"texto": "SUSHI"})
    assert "lista.md:2" in out
    out, err = skills.execute("leer_archivo", {"ruta": "../../../etc/passwd"})
    assert err and "fuera" in out
    out, err = skills.execute("leer_archivo", {"ruta": "/etc/passwd"})
    assert err


def test_media_list(skills):
    skills.execute("registrar_obra", {"titulo": "Frieren", "tipo": "anime", "estado": "viendo", "progreso": "ep 3"})
    out, err = skills.execute(
        "actualizar_obra", {"titulo": "frieren", "progreso": "ep 10", "opinion_ali": "Obra maestra del duelo."}
    )
    assert not err and "ep 10" in out
    assert "Frieren" in skills.briefing()
    out, err = skills.execute("actualizar_obra", {"titulo": "Naruto"})
    assert err


def test_subtitles_are_read_by_time_range(tmp_path):
    srt = tmp_path / "ep1.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n<i>Hola</i>\n\n"
        "2\n00:01:05,000 --> 00:01:07,500\n¿Vamos por ramen?\n\n"
        "3\n00:05:00,000 --> 00:05:02,000\nAdiós\n",
        encoding="utf-8",
    )
    assert read_subtitles(srt, 0, 10) == ["[00:01] Hola"]
    assert read_subtitles(srt, 60, 120) == ["[01:05] ¿Vamos por ramen?"]
