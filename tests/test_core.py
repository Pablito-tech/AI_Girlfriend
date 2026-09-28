import anthropic
import httpx2
import pytest

from ali.core import Ali
from ali.reflection import Ajuste, ChisteInterno, DatoUsuario, Gusto, NuevoRecuerdo, Reflexion, apply_reflection
from tests.fakes import FakeClient, response, text, tool_use


def drain(events):
    return list(events)


def sample_reflection() -> Reflexion:
    return Reflexion(
        resumen="Hablaron de Toby, su perro, y de su examen del viernes.",
        recuerdos=[
            NuevoRecuerdo(contenido="Tiene examen de cálculo el viernes 2 de octubre", tipo="evento",
                          importancia=7, etiquetas=["escuela"], fecha_seguimiento="2026-10-03"),
        ],
        datos_usuario=[DatoUsuario(clave="ciudad", valor="Monterrey")],
        estilo_comunicacion_usuario="Escribe relajado, con muchos 'jajaja'.",
        ajustes_rasgos=[Ajuste(nombre="jugueton", delta=0.05, motivo="Le siguió todas las bromas")],
        ajustes_humor=[Ajuste(nombre="juegos de palabras", delta=0.05, motivo="Se rió mucho")],
        notas_humor=["Le dan risa los juegos de palabras malos"],
        chistes_internos=[ChisteInterno(chiste="Toby, el perro filósofo", origen="Toby se quedó mirando la pared")],
        gustos_de_ali=[Gusto(tema="perros", opinion="Le parecen los mejores filósofos.")],
        apodos_para_usuario=["cielo"],
        estado_animo="contenta y juguetona",
        nota_relacion="Primera conversación larga; hubo mucha química.",
    )


def test_turn_with_tool_use_saves_memory_and_persists(config, clock):
    client = FakeClient([
        response(
            text("¡Aww, Toby! "),
            tool_use("tu_1", "guardar_recuerdo", {"contenido": "Su perro se llama Toby", "tipo": "hecho", "importancia": 8}),
            stop_reason="tool_use",
        ),
        response(text("Ya quiero conocerlo.")),
    ])
    ali = Ali(config, client=client)
    events = drain(ali.chat("Mi perro se llama Toby"))

    assert [e.text for e in events if e.kind == "tool"] == ["guardar_recuerdo"]
    assert ali.memory.all()[0].content == "Su perro se llama Toby"

    # la segunda llamada incluye el resultado de la herramienta
    second = client.messages.calls[1]["messages"]
    assert second[-1]["content"][0]["tool_use_id"] == "tu_1"
    assert "Guardado" in second[-1]["content"][0]["content"]

    stored = ali.db.recent_messages(10)
    assert [(m["role"], m["content"]) for m in stored] == [
        ("user", "Mi perro se llama Toby"),
        ("assistant", "¡Aww, Toby! Ya quiero conocerlo."),
    ]


def test_request_uses_recommended_settings(config, clock):
    client = FakeClient([response(text("hola"))])
    ali = Ali(config, client=client)
    drain(ali.chat("hola"))
    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "medium"}
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["cache_control"] == {"type": "ephemeral"}
    assert any(t.get("type") == "web_search_20260209" for t in call["tools"])
    # la fecha/hora va en el mensaje (no en el prompt de sistema) para no romper la caché
    first_block = call["messages"][0]["content"][0]["text"]
    assert first_block.startswith("<contexto_ali>") and "27 de septiembre de 2026" in first_block
    assert "27 de septiembre de 2026, 18:30" not in call["system"]


def test_memories_come_to_mind_when_relevant(config, clock):
    client = FakeClient([response(text("¡Claro que me acuerdo de Toby!"))])
    ali = Ali(config, client=client)
    ali.memory.add("Su perro se llama Toby y es un beagle", "hecho", 8)
    drain(ali.chat("¿te acuerdas de mi perro?"))
    context = client.messages.calls[0]["messages"][-1]["content"][0]["text"]
    assert "Toby" in context


def test_everything_survives_a_restart(config, clock):
    first = Ali(config, client=FakeClient([response(text("¡Hola, Sam!"))]))
    first.personality.set_user_name("Sam")
    drain(first.chat("hola, soy Sam"))
    first.memory.add("Sam estudia ingeniería", "hecho", 7)
    first.end_session()  # sin reflexión configurada devuelve None, pero guarda todo
    first.close()

    clock.advance(days=2)
    client = FakeClient([response(text("¡Volviste!"))])
    second = Ali(config, client=client)
    drain(second.chat("ya volví"))
    call = client.messages.calls[0]
    assert "Se llama Sam." in call["system"]
    assert "hace 2 días" in call["system"]
    # los mensajes de la vez pasada se cargan como historial
    assert call["messages"][0] == {"role": "user", "content": "hola, soy Sam"}
    assert "conversación nueva" in call["messages"][-1]["content"][0]["text"]
    assert second.memory.count() == 1
    assert second.personality.relationship["sesiones"] == 2


def test_reflection_consolidates_memories_and_evolves_personality(config, clock):
    client = FakeClient([response(text("jajaja Toby es un filósofo"))], parsed=sample_reflection())
    ali = Ali(config, client=client)
    drain(ali.chat("Toby se quedó viendo la pared 10 minutos jajaja"))
    result = ali.end_session()

    assert result is not None
    p = ali.personality
    assert p.state["rasgos"]["jugueton"]["valor"] == 0.75
    assert p.state["humor"]["estilos"]["juegos de palabras"] == 0.55
    assert p.user["datos"]["ciudad"] == "Monterrey"
    assert p.user["apodos"] == ["cielo"]
    assert p.state["animo"]["estado"] == "contenta y juguetona"
    assert any(m.follow_up_at for m in ali.memory.all())
    assert ali.pending_reflections() == []

    parse_call = client.messages.parse_calls[0]
    assert "Toby se quedó viendo la pared" in parse_call["messages"][0]["content"]
    assert parse_call["output_config"] == {"effort": "high"}

    # la próxima sesión arranca con el resumen y la broma interna en su prompt
    clock.advance(days=1)
    client2 = FakeClient([response(text("¡Hola cielo!"))])
    again = Ali(config, client=client2)
    drain(again.chat("hola"))
    system = client2.messages.calls[0]["system"]
    assert "Hablaron de Toby" in system and "Toby, el perro filósofo" in system


def test_follow_up_is_briefed_when_due(config, clock):
    ali = Ali(config, client=FakeClient())
    ali.start_session()
    apply_reflection(sample_reflection(), ali.memory, ali.personality, ali.session_id)
    ali.end_session()

    clock.advance(days=7)
    client = FakeClient([response(text("¿Cómo te fue en el examen?"))])
    later = Ali(config, client=client)
    drain(later.chat("hola"))
    context = client.messages.calls[0]["messages"][-1]["content"][0]["text"]
    assert "cómo le fue" in context and "examen de cálculo" in context


def test_crashed_session_is_consolidated_next_time(config, clock):
    ali = Ali(config, client=FakeClient([response(text("¡Hola!"))]))
    drain(ali.chat("hola"))
    ali.close()  # se cerró sin end_session (como si se cortara la luz)

    recovered = Ali(config, client=FakeClient([], parsed=sample_reflection()))
    assert len(recovered.pending_reflections()) == 1
    assert recovered.consolidate_pending() == 1
    assert recovered.pending_reflections() == []


def test_refusal_and_api_errors_roll_back_the_turn(config, clock):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    client = FakeClient([
        response(stop_reason="refusal"),
        anthropic.APIConnectionError(request=request),
        response(text("Aquí sigo")),
    ])
    ali = Ali(config, client=client)

    events = drain(ali.chat("algo que se rechaza"))
    assert any(e.kind == "refusal" for e in events)
    assert ali.messages == [] and ali.db.recent_messages(10) == []

    events = drain(ali.chat("hola"))
    assert any(e.kind == "error" and "conexión" in e.text for e in events)
    assert ali.messages == []

    drain(ali.chat("hola de nuevo"))
    assert [m["content"] for m in ali.db.recent_messages(10)] == ["hola de nuevo", "Aquí sigo"]


def test_greeting_does_not_store_a_fake_user_message(config, clock):
    client = FakeClient([response(text("¡Buenas tardes! ¿Cómo va tu día?"))])
    ali = Ali(config, client=client)
    drain(ali.greet())
    assert [(m["role"], m["content"]) for m in ali.db.recent_messages(10)] == [
        ("assistant", "¡Buenas tardes! ¿Cómo va tu día?")
    ]


def test_long_sessions_are_trimmed_at_a_safe_point(config, clock):
    config.max_session_messages = 6
    client = FakeClient([response(text(f"r{i}")) for i in range(6)])
    ali = Ali(config, client=client)
    for i in range(6):
        drain(ali.chat(f"m{i}"))
    assert len(ali.messages) <= 6
    assert ali.messages[0]["role"] == "user"


def test_export_writes_everything(config, clock):
    ali = Ali(config, client=FakeClient())
    ali.memory.add("Le gusta el café de olla", "preferencia", 5)
    path = ali.export()
    data = path.read_text(encoding="utf-8")
    assert "café de olla" in data and '"personalidad"' in data


@pytest.mark.parametrize("model,expect_thinking,expect_fallbacks", [
    ("claude-opus-5", True, True),
    ("claude-sonnet-5", True, False),
    ("claude-haiku-4-5", False, False),
])
def test_model_specific_parameters(config, clock, model, expect_thinking, expect_fallbacks):
    config.model = model
    client = FakeClient([response(text("hola"))])
    drain(Ali(config, client=client).chat("hola"))
    call = client.messages.calls[0]
    assert ("thinking" in call) == expect_thinking
    assert ("fallbacks" in call) == expect_fallbacks


def test_unexpected_errors_do_not_crash_the_chat(config, clock):
    client = FakeClient([RuntimeError("algo raro"), response(text("Sigo aquí"))])
    ali = Ali(config, client=client)
    events = drain(ali.chat("hola"))
    assert any(e.kind == "error" and "algo raro" in e.text for e in events)
    assert ali.messages == []
    drain(ali.chat("¿sigues ahí?"))
    assert ali.db.recent_messages(10)[-1]["content"] == "Sigo aquí"


def test_failed_first_turn_keeps_the_briefing_for_the_retry(config, clock):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    client = FakeClient([anthropic.APIConnectionError(request=request), response(text("¡Hola!"))])
    ali = Ali(config, client=client)
    ali.start_session()
    ali.skills.execute("agregar_evento", {"titulo": "Cita con el dentista", "fecha_hora": "2026-09-28T09:00"})
    ali._briefing = ali.skills.briefing()

    drain(ali.chat("hola"))            # falla
    drain(ali.chat("hola otra vez"))   # reintento: el briefing sigue ahí
    assert "Cita con el dentista" in client.messages.calls[-1]["messages"][-1]["content"][0]["text"]


@pytest.mark.parametrize("key,expected", [
    ("sk-ant-api03-" + "a" * 90 + "AA", None),
    ("sk-ant-api03-AbCdE...xYz", "..."),
    ("sk-ant-api03-AbCdE…xYz", "..."),
    ("sk-ant-api03-" + "a" * 40 + " " + "b" * 40, "espacios"),
    ("clave-" + "a" * 90, "sk-ant-"),
    ("sk-ant-api03-abc", "incompleta"),
])
def test_api_key_problems_are_explained(config, key, expected):
    config.api_key = key
    problem = config.api_key_problem()
    assert (problem is None) if expected is None else (expected in problem)
