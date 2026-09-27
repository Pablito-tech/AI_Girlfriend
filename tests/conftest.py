"""Fixtures compartidas: reloj congelado y configuración temporal."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ali import utils  # noqa: E402
from ali.config import PROJECT_ROOT, Config  # noqa: E402


class Clock:
    def __init__(self, start: datetime):
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


@pytest.fixture
def clock():
    c = Clock(datetime(2026, 9, 27, 18, 30, 0))
    utils.set_clock(c)
    yield c
    utils.set_clock(None)


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        api_key="test",
        data_dir=tmp_path / "data",
        files_dir=tmp_path / "data" / "archivos",
        persona_file=PROJECT_ROOT / "config" / "persona.toml",
        greeting=False,
        reflect_every=0,
    )
