"""SAQEF platform adapters registry."""
from platforms.base import Adapter, IsolationPolicy, load_adapter  # noqa: F401


def get_adapter(name):
    return load_adapter(name)
