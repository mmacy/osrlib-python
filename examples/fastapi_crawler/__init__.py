"""The FastAPI wrapper: the barrow behind an HTTP API — the second front-end proof.

The same adventure the TUI crawler serves at a terminal, served over HTTP: run it
with `uv run uvicorn examples.fastapi_crawler:app` (this package exposes `app`, the
ASGI entry point uvicorn targets). The patterns the example demonstrates live in
[`app`][examples.fastapi_crawler.app]: the per-session lock, sync endpoints,
player-visibility filtering at the wire, in-fiction rejections as 200s, the typed
error hierarchy mapped to HTTP statuses, and save-by-id with the save document never
leaving the server.
"""

from .app import app

__all__ = ["app"]
