"""Deprecated — superseded by ``apollo``.

Kept as a thin re-export so external callers / tests referencing
``StubLinkedInSource`` continue to work while we migrate.
"""

from __future__ import annotations

from .apollo import StubApolloSource as StubLinkedInSource

__all__ = ["StubLinkedInSource"]
