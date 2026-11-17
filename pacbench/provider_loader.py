"""Resolve the installed Hermes memory provider across bundled and catalog releases."""
from importlib import import_module


def provider_module(name, submodule=None):
    memory = import_module("plugins.memory")
    resolver = getattr(memory, "import_provider_module", None)
    if resolver is not None:
        return resolver(name, submodule)
    suffix = f".{submodule}" if submodule else ""
    return import_module(f"plugins.memory.{name}{suffix}")
