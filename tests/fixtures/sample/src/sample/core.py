"""Engine that does the work."""

from __future__ import annotations

from .util import clamp


class Base:
    """Base class."""

    def step(self, n: int) -> int:
        return n


class Engine(Base):
    """Runs steps."""

    def step(self, n: int) -> int:
        return clamp(n + 1)

    async def spin(self, n: int) -> int:
        return self.step(n)


def run(n: int) -> int:
    """Run the engine once."""
    return Engine().step(n)
