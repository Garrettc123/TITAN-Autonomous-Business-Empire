"""
GARCAR FULL-STACK PLATFORM LAYER
=================================
Drops a production-grade FastAPI application shell onto any
Garcar system, giving it:

  - REST API with versioned routes
  - WebSocket real-time event stream
  - Health + readiness endpoints
  - Auth middleware (JWT)
  - Rate limiting middleware
  - Inter-system event ingestion endpoint
  - Metrics endpoint (Prometheus-compatible)
  - Mastery improvement loop
  - Self-upgrade hook

Usage (any Garcar system):
    from full_stack_platform import GarcarPlatform, run
    platform = GarcarPlatform(system_id="my-system", domain="finance")
    platform.register_domain_routes(my_router)
    run(platform)
"""

import asyncio
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

from garcar_api_bank import GarcarAPIBank, GarcarConfig
from system_registry import register_this_system, get_registry

logger = logging.getLogger("garcar.platform")

try:
    from fastapi import FastAPI, Request, HTTPException, WebSocket, Depends
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    logger.warning("FastAPI not installed — HTTP server disabled.")


class MasteryEngine:
    """
    Tracks mastery level for this system and triggers
    improvement routines when a new mastery threshold is crossed.
    """

    LEVELS = [
        (0,    "Initializing"),
        (100,  "Operational"),
        (500,  "Proficient"),
        (2000, "Advanced"),
        (10000,"Expert"),
        (50000,"Master"),
        (float('inf'), "Transcendent"),
    ]

    def __init__(self):
        self.score = 0
        self.level_name = "Initializing"
        self._improvement_hooks: List[Callable] = []
        self._last_level_idx = 0

    def add_score(self, points: int):
        self.score += points
        for i, (threshold, name) in enumerate(self.LEVELS):
            if self.score < threshold:
                if i != self._last_level_idx:
                    self._last_level_idx = i
                    self.level_name = self.LEVELS[i - 1][1]
                    asyncio.ensure_future(self._trigger_improvements())
                break

    def register_improvement_hook(self, hook: Callable):
        self._improvement_hooks.append(hook)

    async def _trigger_improvements(self):
        logger.info(f"🏆 MASTERY LEVEL UP → {self.level_name} (score={self.score})")
        for hook in self._improvement_hooks:
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(self.level_name, self.score)
                else:
                    hook(self.level_name, self.score)
            except Exception as e:
                logger.error(f"Improvement hook error: {e}")

    def status(self) -> Dict:
        return {"score": self.score, "level": self.level_name,
                "next_threshold": next(
                    (t for t, _ in self.LEVELS if t > self.score), None)}


class GarcarPlatform:
    """
    Full-stack platform wrapper for any Garcar system.
    Provides HTTP API, WebSocket stream, health, auth, metrics.
    """

    def __init__(self, system_id: str, domain: str,
                 name: str = None, capabilities: List[str] = None,
                 tags: List[str] = None, port: int = 8000):
        GarcarConfig.SYSTEM_ID = system_id
        self.system_id = system_id
        self.domain = domain
        self.name = name or system_id
        self.port = port
        self.bank = GarcarAPIBank(system_id=system_id)
        self.mastery = MasteryEngine()
        self._start_time = time.time()
        self._domain_routers: List[Any] = []
        self._ws_clients: List[WebSocket] = [] if FASTAPI_AVAILABLE else []

        # Register in global registry
        register_this_system(
            name=self.name, domain=domain,
            capabilities=capabilities or ["api", "events", "health"],
            tags=tags or [], bank=self.bank
        )

        if FASTAPI_AVAILABLE:
            self.app = self._build_app()
        else:
            self.app = None

    def _build_app(self) -> "FastAPI":
        app = FastAPI(
            title=f"Garcar — {self.name}",
            description=f"Full-stack platform node | domain={self.domain}",
            version=GarcarConfig.API_BANK_VERSION,
            docs_url="/docs",
            redoc_url="/redoc"
        )

        app.add_middleware(CORSMiddleware, allow_origins=["*"],
                           allow_methods=["*"], allow_headers=["*"])

        @app.middleware("http")
        async def auth_and_rate(request: Request, call_next):
            # Skip auth for public endpoints
            public = ["/health", "/ready", "/metrics", "/docs",
                      "/redoc", "/openapi.json"]
            if request.url.path not in public:
                token = request.headers.get("Authorization", "").replace("Bearer ", "")
                if token and not self.bank.auth.verify_token(token):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)
                if not self.bank.rate_limiter.allow(str(request.client.host)):
                    return JSONResponse({"error": "Rate limit exceeded"}, status_code=429)
            response = await call_next(request)
            self.bank.telemetry.increment("http_requests")
            self.mastery.add_score(1)
            return response

        # ── Core endpoints ──────────────────────

        @app.get("/health")
        async def health():
            report = self.bank.telemetry.health()
            return {"status": report.status,
                    "system_id": self.system_id,
                    "uptime": report.uptime_seconds,
                    "version": GarcarConfig.API_BANK_VERSION}

        @app.get("/ready")
        async def ready():
            return {"ready": True, "system_id": self.system_id}

        @app.get("/metrics")
        async def metrics():
            health = self.bank.telemetry.health()
            return {"system_id": self.system_id,
                    "metrics": health.metrics,
                    "mastery": self.mastery.status(),
                    "uptime": health.uptime_seconds}

        @app.get("/status")
        async def status():
            registry = get_registry(self.bank)
            return {"platform": self.bank.status(),
                    "mastery": self.mastery.status(),
                    "registry": registry.network_status()}

        @app.post("/events")
        async def ingest_event(request: Request):
            """Receive inter-system events from other Garcar nodes."""
            body = await request.json()
            await self.bank.messaging.emit(
                body.get("type", "unknown"),
                body.get("payload", {})
            )
            self.bank.telemetry.increment("events_received")
            return {"accepted": True, "event_type": body.get("type")}

        @app.websocket("/stream")
        async def ws_stream(websocket: WebSocket):
            await websocket.accept()
            self._ws_clients.append(websocket)
            try:
                while True:
                    data = await websocket.receive_text()
                    await self.bank.messaging.emit("ws.message", {"data": data})
            except Exception:
                self._ws_clients.remove(websocket)

        @app.get("/registry")
        async def registry_view():
            return get_registry(self.bank).network_status()

        return app

    def register_domain_routes(self, router: Any):
        """Attach domain-specific routes (FastAPI APIRouter)."""
        if self.app and FASTAPI_AVAILABLE:
            self.app.include_router(router, prefix="/api/v1")
            logger.info(f"Registered domain routes for {self.system_id}")

    async def broadcast_event(self, event_type: str, payload: Dict):
        """Broadcast event to all WebSocket clients."""
        import json
        dead = []
        for ws in self._ws_clients:
            try:
                await ws.send_text(json.dumps({"type": event_type, "payload": payload}))
            except Exception:
                dead.append(ws)
        for d in dead:
            self._ws_clients.remove(d)

    async def start_background_tasks(self):
        """Start heartbeat, telemetry push, and mastery scoring loops."""
        async def heartbeat_loop():
            registry = get_registry(self.bank)
            while True:
                registry.heartbeat(self.system_id)
                await self.bank.telemetry.push_health()
                self.mastery.add_score(10)  # alive score
                await asyncio.sleep(30)

        asyncio.ensure_future(heartbeat_loop())
        await self.bank.startup()
        logger.info(f"Background tasks started for {self.system_id}")

    def run(self, host: str = "0.0.0.0"):
        """Start the full-stack HTTP server."""
        if not FASTAPI_AVAILABLE:
            logger.error("FastAPI/uvicorn not installed. Run: pip install fastapi uvicorn")
            return

        async def _run():
            await self.start_background_tasks()
            config = uvicorn.Config(self.app, host=host, port=self.port,
                                    log_level="info")
            server = uvicorn.Server(config)
            await server.serve()

        asyncio.run(_run())


def run(platform: GarcarPlatform, host: str = "0.0.0.0"):
    """Convenience entry-point."""
    platform.run(host=host)
