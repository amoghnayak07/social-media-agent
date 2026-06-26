"""Platform integration layer — the ONLY platform-aware code in the app.

Everything above this layer (routers, agent, autonomy gate) operates on the
normalized data model and must never reference a platform's API shapes. Adding a
platform later means writing a new adapter here, not changing anything above.
"""
