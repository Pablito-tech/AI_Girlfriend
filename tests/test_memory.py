from datetime import datetime, timedelta

from ali.memory import MemoryStore
from ali.storage import Database
from ali.utils import bm25_scores, tokenize


def test_tokenize_normalizes_accents_plurals_and_stopwords():
    assert tokenize("¡Me encantan los ANIMES de acción!") == ["encanta", "anim", "accion"]
    assert tokenize("anime") == tokenize("animes")
    assert tokenize("perro") == tokenize("perros") == tokenize("perra")
    assert tokenize("ciudad") == tokenize("ciudades")


def test_bm25_prefers_documents_with_rare_query_terms():
    docs = [tokenize("le gusta el ramen"), tokenize("su perro se llama Toby"), tokenize("le gusta el sushi")]
    scores = bm25_scores(tokenize("como se llama tu perro"), docs)
    assert scores.index(max(scores)) == 1


def test_search_ranks_by_relevance_and_reinforces(clock):
    store = MemoryStore(Database(":memory:"))
    store.add("A su pareja le encanta el ramen picante", "preferencia", 6, ["comida"])
    store.add("Tiene examen de cálculo el viernes", "evento", 7, ["escuela"])
    dog, _ = store.add("Su perro se llama Toby", "hecho", 8, ["mascota"])

    results = store.search("¿cómo se llama tu perro?")
    assert results and results[0].memory.id == dog.id
    assert store.get(dog.id).access_count == 1  # recordarlo lo refuerza


def test_duplicates_are_merged_instead_of_repeated(clock):
    store = MemoryStore(Database(":memory:"))
    first, new1 = store.add("Su perro se llama Toby", "hecho", 6)
    second, new2 = store.add("Su perro se llama Toby y es un beagle", "hecho", 8, ["mascota"])
    assert new1 and not new2
    assert first.id == second.id
    assert store.count() == 1
    merged = store.get(first.id)
    assert "beagle" in merged.content and merged.importance == 8 and merged.access_count == 1


def test_retention_decays_and_recall_makes_memories_last_longer(clock):
    store = MemoryStore(Database(":memory:"))
    mem, _ = store.add("Fueron juntos (virtualmente) a ver las estrellas", "momento", 8)
    assert mem.retention() == 1.0

    clock.advance(days=90)
    faded = store.get(mem.id).retention()
    assert 0 < faded < 1

    half_life_before = store.get(mem.id).half_life_days()
    store.touch([mem.id])
    refreshed = store.get(mem.id)
    assert refreshed.retention() == 1.0
    assert refreshed.half_life_days() > half_life_before


def test_follow_ups_become_due_after_their_date(clock):
    store = MemoryStore(Database(":memory:"))
    mem, _ = store.add("Tiene entrevista de trabajo", "evento", 8, follow_up_at=clock() + timedelta(days=2))
    assert store.due_follow_ups() == []
    clock.advance(days=3)
    assert [m.id for m in store.due_follow_ups()] == [mem.id]
    store.mark_followed_up([mem.id])
    assert store.due_follow_ups() == []


def test_anniversaries_and_fading_important(clock):
    store = MemoryStore(Database(":memory:"))
    first, _ = store.add("Primera vez que vieron anime juntos: Frieren", "momento", 9)
    clock.t = datetime(2027, 9, 27, 10, 0)
    assert [m.id for m in store.anniversaries()] == [first.id]
    assert store.fading_important().id == first.id  # un año sin recordarlo: se estaba desvaneciendo


def test_memories_survive_restart(tmp_path, clock):
    path = tmp_path / "ali.db"
    db = Database(path)
    MemoryStore(db).add("Su color favorito es el verde", "preferencia", 5)
    db.close()

    reopened = MemoryStore(Database(path))
    assert [m.content for m in reopened.all()] == ["Su color favorito es el verde"]


def test_strict_search_ignores_words_that_are_everywhere(clock):
    store = MemoryStore(Database(":memory:"))
    for i, thing in enumerate(["ramen", "gatos", "lluvia", "cafe", "videojuegos", "playa", "jazz", "pizza"]):
        store.add(f"A su pareja le gusta {thing}", "preferencia", 5, [f"tema{i}"])
    # "pareja" está en todos los recuerdos: por sí sola no es una pista
    assert store.search("¿qué opinas de tu pareja?", strict=True) == []
    assert store.search("¿qué opinas de tu pareja?", strict=False) != []
    # una pista real sí trae el recuerdo
    hits = store.search("hoy llueve, qué rico día de lluvia", strict=True)
    assert hits and "lluvia" in hits[0].memory.content
