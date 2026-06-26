"""YouTube integration. All YouTube/Google API specifics live under this package.

Phase 3a: OAuth (this layer's `oauth` module). Phase 3b adds the read client.
Nothing here leaks above the integration layer — callers receive normalized data
or typed AppErrors, never raw Google API shapes.
"""
