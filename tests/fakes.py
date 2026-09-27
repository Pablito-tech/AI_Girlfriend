"""Cliente de Claude falso para probar sin gastar la API."""

from __future__ import annotations

import copy
from types import SimpleNamespace


# --- respuestas falsas ------------------------------------------------------


def text(t: str):
    return SimpleNamespace(type="text", text=t)


def tool_use(id: str, name: str, input: dict):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def response(*blocks, stop_reason: str = "end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


class FakeStream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        if isinstance(self.resp, Exception):
            raise self.resp
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for block in self.resp.content:
            if block.type == "text":
                yield SimpleNamespace(type="text", text=block.text)

    def get_final_message(self):
        return self.resp


class FakeMessages:
    def __init__(self, responses=(), parsed=None):
        self.responses = list(responses)
        self.parsed = parsed
        self.calls: list[dict] = []
        self.parse_calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return FakeStream(self.responses.pop(0))

    def parse(self, **kwargs):
        self.parse_calls.append(copy.deepcopy(kwargs))
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.parsed)


class FakeClient:
    def __init__(self, responses=(), parsed=None):
        self.messages = FakeMessages(responses, parsed)
        self.beta = SimpleNamespace(messages=self.messages)
