"""Deprecated — superseded by ``anthropic_news``.

Kept as a thin re-export so external callers / tests referencing
``StubGoogleNewsSource`` continue to work while we migrate.
"""

from __future__ import annotations

from .anthropic_news import StubAnthropicNewsSource as StubGoogleNewsSource

__all__ = ["StubGoogleNewsSource"]
