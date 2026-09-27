"""Conexión con Claude: conversación en streaming con herramientas y reflexión.

Este módulo es lo único que habla con la API de Anthropic. Si algún día
quieres usar otra forma de llamar al modelo, sólo tienes que cambiar aquí.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, TypeVar

import anthropic

from .config import Config

T = TypeVar("T")

# Si el modelo rechaza una petición por sus filtros de seguridad, la API la
# reintenta sola en el modelo de respaldo que Anthropic recomienda.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_ROUNDS = 12
MAX_PAUSE_CONTINUATIONS = 5
MAX_JSON_RETRIES = 2


@dataclass
class Event:
    """Algo que pasa mientras Ali responde (para mostrarlo en la interfaz)."""

    kind: str          # round | text | tool | notice | refusal | retry | error
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def _is_legacy(model: str) -> bool:
    """Modelos anteriores a 4.6 (p. ej. Haiku 4.5) no usan pensamiento adaptativo ni `effort`."""
    return model.startswith("claude-haiku") or any(
        model.startswith(p) for p in ("claude-3", "claude-sonnet-4-5", "claude-opus-4-5", "claude-opus-4-1", "claude-sonnet-4-0", "claude-opus-4-0")
    )


def supports_fallbacks(model: str) -> bool:
    return model.startswith(("claude-opus-5", "claude-fable-5"))


class ClaudeBrain:
    def __init__(self, config: Config, client: Any | None = None):
        self.config = config
        self.client = client or anthropic.Anthropic(api_key=config.api_key)

    # ------------------------------------------------------------------

    def _base_kwargs(self, effort: str) -> dict[str, Any]:
        model = self.config.model
        kw: dict[str, Any] = {"model": model}
        if not _is_legacy(model):
            kw["thinking"] = {"type": "adaptive"}
            kw["output_config"] = {"effort": effort}
        if self.config.fallbacks and supports_fallbacks(model):
            kw["betas"] = [FALLBACK_BETA]
            kw["fallbacks"] = "default"
        return kw

    def server_tools(self) -> list[dict[str, Any]]:
        if not self.config.web_search:
            return []
        version = "web_search_20250305" if _is_legacy(self.config.model) else "web_search_20260209"
        return [{"type": version, "name": "web_search", "max_uses": 3}]

    # ------------------------------------------------------------------

    def run_turn(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        execute_tool: Callable[[str, Any], tuple[Any, bool]],
    ) -> Iterator[Event]:
        """Ejecuta un turno completo: streaming de texto + bucle de herramientas.

        `messages` se modifica en el lugar (sólo se le agregan mensajes al
        final), así la conversación queda lista para el siguiente turno.
        """
        all_tools = tools + self.server_tools()
        extra: dict[str, Any] = {}
        rounds = pauses = json_retries = 0

        while True:
            yield Event("round")  # empieza una llamada al modelo
            try:
                with self.client.beta.messages.stream(
                    max_tokens=self.config.max_tokens,
                    system=system,
                    messages=messages,
                    tools=all_tools,
                    cache_control={"type": "ephemeral"},  # caché automática del prefijo
                    **self._base_kwargs(self.config.effort),
                    **extra,
                ) as stream:
                    for event in stream:
                        if event.type == "text":
                            yield Event("text", event.text)
                        elif event.type == "content_block_start" and event.content_block.type == "server_tool_use":
                            yield Event("tool", event.content_block.name)  # p. ej. búsqueda web
                    response = stream.get_final_message()
                json_retries = 0
            except ValueError:
                # Con eager_input_streaming, un JSON de herramienta imposible de
                # leer se detecta aquí. No hay tool_use_id que responder: se
                # repite el turno (con límite). Los errores de la API no son
                # ValueError y siguen su camino.
                json_retries += 1
                if json_retries > MAX_JSON_RETRIES:
                    raise
                yield Event("retry", "Se me trabó una idea, déjame intentarlo de nuevo…")
                continue

            if response.stop_reason == "refusal":
                yield Event("refusal", "Prefiero no responder a eso.")
                return

            if response.stop_reason == "pause_turn":  # una búsqueda web larga quedó en pausa
                messages.append({"role": "assistant", "content": response.content})
                pauses += 1
                if pauses > MAX_PAUSE_CONTINUATIONS:
                    return
                continue

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if tool_uses and response.stop_reason == "max_tokens":
                # Los argumentos quedaron cortados: no se ejecuta nada.
                yield Event("notice", "La respuesta se cortó antes de terminar.")
                return

            messages.append({"role": "assistant", "content": response.content})
            if not tool_uses:
                return

            rounds += 1
            results = []
            for block in tool_uses:
                yield Event("tool", block.name, {"input": block.input})
                output, is_error = execute_tool(block.name, block.input)
                result: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id, "content": output}
                if is_error:
                    result["is_error"] = True
                results.append(result)
            if rounds >= MAX_TOOL_ROUNDS:
                extra["tool_choice"] = {"type": "none"}  # ya usó muchas: ahora tiene que responder
            messages.append({"role": "user", "content": results})

    # ------------------------------------------------------------------

    def structured(self, system: str, prompt: str, output_format: type[T]) -> T | None:
        """Pide una respuesta con formato fijo (se usa para la reflexión)."""
        response = self.client.beta.messages.parse(
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=output_format,
            **self._base_kwargs(self.config.reflection_effort),
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            return None
        return response.parsed_output
