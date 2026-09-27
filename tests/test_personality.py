import json

from ali.personality import Personality


def load(config):
    return Personality.load(config.persona_file, config.personality_path)


def test_initial_state_comes_from_persona(config, clock):
    p = load(config)
    assert p.name == "Ali"
    assert p.state["rasgos"]["jugueton"] == {"valor": 0.7, "base": 0.7}
    assert any(g["tema"] == "anime" for g in p.state["gustos"])
    assert config.personality_path.exists()


def test_traits_evolve_slowly_and_within_limits(config, clock):
    p = load(config)
    p.adjust_trait("sarcasmo", 0.5)            # se limita a +0.05 por ajuste
    assert p.state["rasgos"]["sarcasmo"]["valor"] == 0.4
    for _ in range(50):
        p.adjust_trait("sarcasmo", 0.05)       # nunca más de max_deriva (0.3) sobre la base (0.35)
    assert p.state["rasgos"]["sarcasmo"]["valor"] == 0.65
    assert not p.adjust_trait("rasgo_inexistente", 0.05)


def test_humor_and_inside_jokes(config, clock):
    p = load(config)
    p.adjust_humor("Humor Absurdo", 0.05)
    assert p.state["humor"]["estilos"]["humor absurdo"] == 0.55
    p.adjust_humor("chistes de papá", 0.05)    # estilo nuevo
    assert p.state["humor"]["estilos"]["chistes de papá"] == 0.35
    assert p.add_inside_joke("El ramen de las 2am", "Se desvelaron comiendo ramen")
    assert not p.add_inside_joke("el ramen de las 2AM", "repetida")


def test_personality_persists_between_runs(config, clock):
    p = load(config)
    p.set_user_name("Sam")
    p.adjust_trait("ternura", 0.05)
    p.add_taste("Chainsaw Man", "Le parece caótico pero genial.")
    p.register_session_start()
    p.save()

    again = load(config)
    assert again.user_name == "Sam"
    assert again.state["rasgos"]["ternura"]["valor"] == 0.8
    assert again.state["gustos"][-1]["tema"] == "Chainsaw Man"
    assert again.relationship["sesiones"] == 1


def test_evolved_values_are_reclamped_when_persona_changes(config, clock, tmp_path):
    p = load(config)
    for _ in range(10):
        p.adjust_trait("jugueton", 0.05)
    p.save()
    assert p.state["rasgos"]["jugueton"]["valor"] == 1.0

    persona = config.persona_file.read_text(encoding="utf-8").replace(
        "[rasgos.jugueton]\nvalor = 0.7", "[rasgos.jugueton]\nvalor = 0.3"
    )
    new_persona = tmp_path / "persona.toml"
    new_persona.write_text(persona, encoding="utf-8")
    again = Personality.load(new_persona, config.personality_path)
    assert again.state["rasgos"]["jugueton"] == {"valor": 0.6, "base": 0.3}
    assert json.loads(config.personality_path.read_text(encoding="utf-8"))["rasgos"]["jugueton"]["base"] == 0.3


def test_familiarity_grows_with_time_together(config, clock):
    p = load(config)
    assert p.familiarity_level()[0] == "recién se conocen"
    p.relationship["sesiones"] = 30
    p.relationship["mensajes"] = 900
    assert p.familiarity_level()[0] == "inseparables"
