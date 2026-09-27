"""Algoritmo de memoria de Ali.

Cómo funciona (inspirado en la memoria humana y en "Generative Agents"):

1. **Guardar**: cada recuerdo tiene un tipo (evento, hecho, momento...), una
   importancia del 1 al 10 y etiquetas. Si ya existe uno casi igual, en vez de
   duplicarlo se *refuerza* (se actualiza y se vuelve más fuerte).

2. **Olvido natural (retención)**: la fuerza de un recuerdo decae con el tiempo
   siguiendo una curva de olvido exponencial:

       retención = 0.5 ** (días_desde_último_uso / vida_media)

   La vida media depende del tipo (un dato tuyo dura más que una nota suelta),
   de la importancia y de cuántas veces se ha recordado. Cada vez que Ali
   recuerda algo, la vida media crece (como la repetición espaciada), así que
   lo que importa se vuelve prácticamente permanente. Nada se borra: un
   recuerdo débil sólo "cuesta más" que salga a la luz.

3. **Recordar**: ante cada mensaje se buscan los recuerdos relevantes y se
   ordenan por:

       puntaje = 0.55·relevancia + 0.20·importancia + 0.25·retención

   La relevancia usa BM25 (búsqueda por palabras clave, sin dependencias). Si
   algún día quieres búsqueda semántica con embeddings, sólo cambia
   `MemoryStore.relevance()`.

4. **Recuerdo espontáneo**: al empezar una sesión Ali revisa aniversarios,
   seguimientos pendientes ("¿cómo te fue en el examen?") y, a veces, rescata
   un recuerdo importante que se está desvaneciendo para mantenerlo vivo.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from .storage import Database
from .utils import bm25_scores, hace_cuanto, iso, jaccard, now, parse_dt, tokenize

# tipo -> (descripción, vida media base en días)
KINDS: dict[str, tuple[str, float]] = {
    "hecho": ("dato sobre el usuario (nombre, trabajo, familia, fechas...)", 365),
    "preferencia": ("algo que le gusta o disgusta al usuario", 180),
    "momento": ("momento especial o emotivo que compartieron", 120),
    "evento": ("algo que pasó o va a pasar en la vida del usuario", 30),
    "opinion": ("opinión o gusto propio de Ali", 180),
    "nota": ("detalle suelto o de poca duración", 14),
}

W_RELEVANCE = 0.55
W_IMPORTANCE = 0.20
W_RETENTION = 0.25
DUPLICATE_THRESHOLD = 0.6
COMMON_TERM_RATIO = 0.3   # en modo estricto, palabras presentes en >30% de los recuerdos no cuentan


@dataclass
class Memory:
    id: int
    content: str
    kind: str
    importance: int
    tags: list[str]
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    follow_up_at: datetime | None = None
    followed_up: bool = False
    source: str = "chat"
    tokens: list[str] = field(default_factory=list, repr=False)

    @classmethod
    def from_row(cls, row) -> "Memory":
        return cls(
            id=row["id"],
            content=row["content"],
            kind=row["kind"],
            importance=row["importance"],
            tags=[t for t in row["tags"].split(",") if t],
            created_at=parse_dt(row["created_at"]),
            last_accessed=parse_dt(row["last_accessed"]),
            access_count=row["access_count"],
            follow_up_at=parse_dt(row["follow_up_at"]) if row["follow_up_at"] else None,
            followed_up=bool(row["followed_up"]),
            source=row["source"],
            tokens=row["tokens"].split(),
        )

    def half_life_days(self) -> float:
        base = KINDS.get(self.kind, KINDS["nota"])[1]
        return base * (0.5 + self.importance / 10) * (1 + 0.5 * self.access_count)

    def retention(self, at: datetime | None = None) -> float:
        """Qué tan "fresco" está el recuerdo (1 = recién vivido, 0 = casi olvidado)."""
        at = at or now()
        days = max(0.0, (at - self.last_accessed).total_seconds() / 86400)
        return 0.5 ** (days / self.half_life_days())

    def render(self, at: datetime | None = None) -> str:
        extra = f" (etiquetas: {', '.join(self.tags)})" if self.tags else ""
        return f"[#{self.id} · {self.kind} · {hace_cuanto(self.created_at, at)}] {self.content}{extra}"


@dataclass
class ScoredMemory:
    memory: Memory
    score: float
    relevance: float


class MemoryStore:
    def __init__(self, db: Database):
        self.db = db

    # --- escritura --------------------------------------------------------

    def add(
        self,
        content: str,
        kind: str = "nota",
        importance: int = 5,
        tags: Iterable[str] = (),
        follow_up_at: datetime | None = None,
        source: str = "chat",
        session_id: int | None = None,
    ) -> tuple[Memory, bool]:
        """Guarda un recuerdo. Devuelve (recuerdo, es_nuevo).

        Si ya existía uno muy parecido del mismo tipo, lo refuerza y lo
        actualiza con la versión nueva en lugar de duplicarlo.
        """
        content = content.strip()
        kind = kind if kind in KINDS else "nota"
        importance = max(1, min(10, int(importance)))
        tags = sorted({t.strip().lower() for t in tags if t and t.strip()})
        tokens = tokenize(content + " " + " ".join(tags))
        stamp = iso(now())

        duplicate = self._find_duplicate(tokens, kind)
        if duplicate:
            merged_tags = sorted(set(duplicate.tags) | set(tags))
            self.db.execute(
                """UPDATE memories SET content = ?, importance = ?, tags = ?, tokens = ?,
                   last_accessed = ?, access_count = access_count + 1,
                   follow_up_at = COALESCE(?, follow_up_at),
                   followed_up = CASE WHEN ? IS NULL THEN followed_up ELSE 0 END
                   WHERE id = ?""",
                (
                    content,
                    max(importance, duplicate.importance),
                    ",".join(merged_tags),
                    " ".join(tokenize(content + " " + " ".join(merged_tags))),
                    stamp,
                    iso(follow_up_at) if follow_up_at else None,
                    iso(follow_up_at) if follow_up_at else None,
                    duplicate.id,
                ),
            )
            return self.get(duplicate.id), False

        mem_id = self.db.execute(
            """INSERT INTO memories (content, kind, importance, tags, tokens, created_at,
               last_accessed, follow_up_at, source, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                content, kind, importance, ",".join(tags), " ".join(tokens), stamp, stamp,
                iso(follow_up_at) if follow_up_at else None, source, session_id,
            ),
        ).lastrowid
        return self.get(mem_id), True

    def _find_duplicate(self, tokens: list[str], kind: str) -> Memory | None:
        best, best_sim = None, 0.0
        for m in self.all(kind=kind):
            sim = jaccard(tokens, m.tokens)
            if sim > best_sim:
                best, best_sim = m, sim
        return best if best_sim >= DUPLICATE_THRESHOLD else None

    def delete(self, memory_id: int) -> bool:
        return self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0

    def touch(self, ids: Iterable[int]) -> None:
        """Refuerza recuerdos: recordarlos los hace más duraderos."""
        stamp = iso(now())
        for mem_id in ids:
            self.db.execute(
                "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                (stamp, mem_id),
            )

    def mark_followed_up(self, ids: Iterable[int]) -> None:
        for mem_id in ids:
            self.db.execute("UPDATE memories SET followed_up = 1 WHERE id = ?", (mem_id,))

    # --- lectura ----------------------------------------------------------

    def get(self, memory_id: int) -> Memory | None:
        row = self.db.query_one("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return Memory.from_row(row) if row else None

    def all(self, kind: str | None = None) -> list[Memory]:
        if kind:
            rows = self.db.query("SELECT * FROM memories WHERE kind = ? ORDER BY id", (kind,))
        else:
            rows = self.db.query("SELECT * FROM memories ORDER BY id")
        return [Memory.from_row(r) for r in rows]

    def count(self) -> int:
        return self.db.query_one("SELECT COUNT(*) AS n FROM memories")["n"]

    def recent(self, limit: int = 10, kind: str | None = None) -> list[Memory]:
        if kind:
            rows = self.db.query(
                "SELECT * FROM memories WHERE kind = ? ORDER BY created_at DESC, id DESC LIMIT ?", (kind, limit)
            )
        else:
            rows = self.db.query("SELECT * FROM memories ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))
        return [Memory.from_row(r) for r in rows]

    @staticmethod
    def relevance(query_tokens: list[str], memories: list[Memory]) -> list[float]:
        """Relevancia 0..1 de cada recuerdo para la consulta (BM25 normalizado).

        Punto de extensión: reemplaza esto por similitud de embeddings si quieres
        búsqueda semántica. Todo lo demás sigue funcionando igual.
        """
        raw = bm25_scores(query_tokens, [m.tokens for m in memories])
        top = max(raw, default=0.0)
        return [s / top if top > 0 else 0.0 for s in raw]

    def search(
        self,
        query: str,
        limit: int = 5,
        kind: str | None = None,
        exclude_ids: Iterable[int] = (),
        min_score: float = 0.25,
        touch: bool = True,
        strict: bool = False,
    ) -> list[ScoredMemory]:
        """Los recuerdos que mejor responden a `query`, ya ordenados.

        `strict=True` se usa para los recuerdos que "vienen a la mente" solos:
        ignora palabras que aparecen en muchísimos recuerdos (tu nombre, "pareja"...)
        para que sólo salgan recuerdos con una pista de verdad.
        """
        at = now()
        all_memories = self.all(kind=kind)
        exclude = set(exclude_ids)
        pool = [m for m in all_memories if m.id not in exclude]
        query_tokens = tokenize(query)
        if strict and len(all_memories) >= 8:
            n = len(all_memories)
            common = {
                t for t in set(query_tokens)
                if sum(t in m.tokens for m in all_memories) / n > COMMON_TERM_RATIO
            }
            query_tokens = [t for t in query_tokens if t not in common]
        if not pool or not query_tokens:
            return []
        results = []
        for m, rel in zip(pool, self.relevance(query_tokens, pool)):
            if rel <= 0:
                continue
            score = W_RELEVANCE * rel + W_IMPORTANCE * (m.importance / 10) + W_RETENTION * m.retention(at)
            if score >= min_score:
                results.append(ScoredMemory(m, score, rel))
        results.sort(key=lambda r: r.score, reverse=True)
        results = results[:limit]
        if touch and results:
            self.touch(r.memory.id for r in results)
        return results

    # --- recuerdos espontáneos -------------------------------------------

    def due_follow_ups(self, at: datetime | None = None) -> list[Memory]:
        at = at or now()
        rows = self.db.query(
            "SELECT * FROM memories WHERE follow_up_at IS NOT NULL AND followed_up = 0 AND follow_up_at <= ?"
            " ORDER BY follow_up_at",
            (iso(at),),
        )
        return [Memory.from_row(r) for r in rows]

    def anniversaries(self, at: datetime | None = None, min_importance: int = 5) -> list[Memory]:
        """Recuerdos que cumplen años hoy (mismo día y mes, años anteriores)."""
        at = at or now()
        return [
            m for m in self.all()
            if m.importance >= min_importance
            and m.created_at.year < at.year
            and (m.created_at.month, m.created_at.day) == (at.month, at.day)
        ]

    def fading_important(self, at: datetime | None = None, rng: random.Random | None = None) -> Memory | None:
        """Elige un recuerdo importante que se está desvaneciendo, para revivirlo."""
        at, rng = at or now(), rng or random.Random()
        candidates = [
            (m, m.importance * (1 - m.retention(at)))
            for m in self.all()
            if m.importance >= 7 and m.kind in ("momento", "evento", "preferencia") and m.retention(at) < 0.5
        ]
        if not candidates:
            return None
        memories, weights = zip(*candidates)
        return rng.choices(memories, weights=weights, k=1)[0]
