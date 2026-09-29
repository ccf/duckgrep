"""Core engine."""
import os
from .sub import helpers
from .sub.helpers import slugify as slug

MAX_RETRIES = 3


class Base:
    def run(self):
        return self.step()

    def step(self):
        return 1


class Engine(Base):
    """Runs jobs."""

    def __init__(self, name: str):
        self.name = slug(name)

    @staticmethod
    def build(cfg: dict) -> "Engine":
        return Engine(cfg["name"])

    def step(self):
        def inner():
            return helpers.normalize(self.name)
        return inner()


def make_engine(name):
    return Engine.build({"name": name})
