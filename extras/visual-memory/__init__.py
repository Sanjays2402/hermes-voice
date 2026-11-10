"""Hermes Visual Memory, as a Hermes plugin: see README.md and vismem/plugin.py."""

try:
    from .vismem.plugin import register
except ImportError:  # imported as a plain module (pytest collecting this folder), not as a plugin package
    from vismem.plugin import register

__all__ = ["register"]
