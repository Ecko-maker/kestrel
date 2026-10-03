"""Kestrel's web console backend (FastAPI).

    GET  /api/session                 who's answering, which tools exist
    GET  /api/traces?q=&limit=        trace list (search)
    GET  /api/traces/{id}             one trace with its spans
    GET  /api/stats                   totals, percentiles and per-request points for charts
    POST /api/traces/{id}/rating      {"rating": "good"|"bad", "note": "..."}
    WS   /ws                          chat: the browser sends messages and approval answers,
                                      the server streams the agent's events back

Security: the server only listens on 127.0.0.1. Every request needs the access token
printed at startup, either once in the URL (?token=..., exchanged for an HttpOnly cookie)
or as "Authorization: Bearer ...". The Host header must be 127.0.0.1/localhost (blocks DNS
rebinding), WebSocket origins are checked, and CORS only allows the Vite dev server.
"""

import asyncio
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from kestrel import trace_report
from kestrel.agent import Agent, EventCallback
from kestrel.tracing import Tracer
from kestrel.web.approver import APPROVAL_TIMEOUT, WebApprover

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = PROJECT_ROOT / "console" / "dist"
COOKIE = "kestrel_token"
DEV_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")
MAX_MESSAGE_CHARS = 20_000

# Builds a fresh Agent for one conversation, wired to its event stream and approver.
AgentFactory = Callable[[EventCallback, WebApprover], Agent]


@dataclass
class WebConfig:
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    port: int = 8765
    static_dir: Path | None = STATIC_DIR
    approval_timeout: float = APPROVAL_TIMEOUT
    session_info: dict = field(default_factory=dict)  # shown in the console header
    extra_hosts: tuple[str, ...] = ()  # e.g. "testserver" in tests

    @property
    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}", *self.extra_hosts}

    @property
    def allowed_origins(self) -> set[str]:
        return {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}", *DEV_ORIGINS}

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?{urlencode({'token': self.token})}"


class Rating(BaseModel):
    rating: str
    note: str = ""


UNAUTHORIZED_PAGE = """<!doctype html><meta charset="utf-8"><title>Kestrel</title>
<body style="font:16px system-ui;max-width:32rem;margin:15vh auto;padding:0 1rem;color:#334">
<h1 style="font-size:1.25rem">Kestrel console</h1>
<p>Open the link printed in the terminal where you ran <code>kestrel web</code>.
It contains a one-time access token, so only you can use this console.</p></body>"""


def create_app(make_agent: AgentFactory, tracer: Tracer, config: WebConfig) -> FastAPI:
    app = FastAPI(title="Kestrel", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config

    def token_ok(candidate: str | None) -> bool:
        return bool(candidate) and secrets.compare_digest(candidate, config.token)

    def authorized(headers, cookies, query) -> bool:
        bearer = headers.get("authorization", "")
        return (token_ok(cookies.get(COOKIE)) or token_ok(query.get("token"))
                or token_ok(bearer.removeprefix("Bearer ") if bearer.startswith("Bearer ") else None))

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in config.allowed_hosts:
            return JSONResponse({"detail": "invalid host"}, status_code=400)
        if request.method == "OPTIONS":  # CORS preflight carries no credentials
            return await call_next(request)
        if not authorized(request.headers, request.cookies, request.query_params):
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "missing or invalid token"}, status_code=401)
            return HTMLResponse(UNAUTHORIZED_PAGE, status_code=401)
        fresh_token = token_ok(request.query_params.get("token"))
        if fresh_token and request.method == "GET" and not request.url.path.startswith("/api/"):
            response = RedirectResponse(request.url.path)  # drop the token from the address bar
        else:
            response = await call_next(request)
        if fresh_token:
            response.set_cookie(COOKIE, config.token, httponly=True, samesite="strict", path="/")
        return response

    app.add_middleware(CORSMiddleware, allow_origins=list(DEV_ORIGINS), allow_credentials=True,
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])

    # --- REST -------------------------------------------------------------------

    @app.get("/api/session")
    def session() -> dict:
        return {"ok": True, **config.session_info}

    @app.get("/api/traces")
    def traces(q: str | None = None, limit: int = 50) -> list[dict]:
        with tracer.connect() as conn:
            return trace_report.list_traces(conn, min(max(limit, 1), 500), q)

    @app.get("/api/traces/{trace_id}")
    def trace(trace_id: str) -> dict:
        try:
            full_id = tracer.find_trace_id(trace_id)
        except LookupError as e:
            raise HTTPException(404, str(e)) from None
        with tracer.connect() as conn:
            return trace_report.get_trace(conn, full_id)

    @app.get("/api/stats")
    def stats() -> dict:
        with tracer.connect() as conn:
            return {**trace_report.compute_stats(conn), "series": trace_report.timeseries(conn)}

    @app.post("/api/traces/{trace_id}/rating")
    def rate(trace_id: str, body: Rating) -> dict:
        if body.rating not in ("good", "bad"):
            raise HTTPException(422, "rating must be 'good' or 'bad'")
        try:
            tracer.rate(tracer.find_trace_id(trace_id), body.rating, body.note[:500])
        except LookupError as e:
            raise HTTPException(404, str(e)) from None
        return {"ok": True}

    # --- Chat over WebSocket ------------------------------------------------------

    @app.websocket("/ws")
    async def chat(ws: WebSocket):
        origin = ws.headers.get("origin")
        if (ws.headers.get("host") not in config.allowed_hosts
                or (origin is not None and origin not in config.allowed_origins)
                or not authorized(ws.headers, ws.cookies, ws.query_params)):
            await ws.close(code=1008)  # policy violation: refused before the handshake completes
            return
        await ws.accept()

        loop = asyncio.get_running_loop()
        outbox: asyncio.Queue = asyncio.Queue()

        def emit(event: dict) -> None:  # called from the agent's worker threads
            loop.call_soon_threadsafe(outbox.put_nowait, event)

        approver = WebApprover(timeout=config.approval_timeout)
        try:
            agent = make_agent(emit, approver)
        except Exception as e:  # e.g. no model configured: the console still shows traces and stats
            agent = None
            await ws.send_json({"type": "error", "message": f"Kestrel can't chat right now: {e}"})
        if agent is not None:
            approver.emit = agent.emit  # approval events carry the run's trace id too
            await ws.send_json({"type": "ready", "session_id": agent.session_id})

        async def forward_events():
            while True:
                await ws.send_json(await outbox.get())

        sender = asyncio.create_task(forward_events())
        running: asyncio.Task | None = None
        try:
            while True:
                message = await ws.receive_json()
                kind = message.get("type")
                if kind == "user_message" and agent is not None:
                    text = str(message.get("text", "")).strip()[:MAX_MESSAGE_CHARS]
                    if not text:
                        continue
                    if running and not running.done():
                        emit({"type": "error", "message": "Kestrel is still working on your last message."})
                        continue
                    running = asyncio.create_task(asyncio.to_thread(agent.run, text))
                elif kind == "approval_response":
                    approver.resolve(str(message.get("approval_id")), message)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            approver.close()  # anything waiting for an answer is rejected; the agent finishes on its own
            sender.cancel()

    # --- The built frontend ---------------------------------------------------------

    static_dir = config.static_dir
    if static_dir and static_dir.is_dir():
        @app.get("/{path:path}")
        def frontend(path: str):
            file = (static_dir / path).resolve()
            if path and file.is_file() and file.is_relative_to(static_dir.resolve()):
                return FileResponse(file)
            return FileResponse(static_dir / "index.html")  # client-side routes
    else:
        @app.get("/")
        def no_frontend():
            return HTMLResponse("<p style='font:16px system-ui;margin:2rem'>The console isn't built yet. "
                                "Run <code>uv run kestrel web --build</code>.</p>")

    return app
