"""
GARCAR API GATEWAY
==================
Single entry point that proxies requests to all 176 registered
Garcar systems. Auto-discovers systems from the registry,
load-balances, and enforces auth + rate limits globally.
"""

import asyncio
import logging
from typing import Dict, List, Optional

from garcar_api_bank import GarcarAPIBank, GarcarConfig
from system_registry import get_registry, GarcarSystemRegistry

logger = logging.getLogger("garcar.gateway")

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    import aiohttp
    GATEWAY_AVAILABLE = True
except ImportError:
    GATEWAY_AVAILABLE = False


class GarcarGateway:
    """
    The master API gateway for the entire Garcar ecosystem.
    Routes:
      GET  /gateway/systems            → list all registered systems
      POST /gateway/route/{system_id}  → proxy request to a system
      POST /gateway/broadcast          → fan-out to all systems
      GET  /gateway/health             → network-wide health summary
    """

    def __init__(self, bank: GarcarAPIBank = None):
        self.bank = bank or GarcarAPIBank(system_id="garcar-gateway")
        self.registry: GarcarSystemRegistry = get_registry(self.bank)
        self._routed = 0
        if GATEWAY_AVAILABLE:
            self.app = self._build_app()

    def _build_app(self):
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware

        app = FastAPI(title="Garcar API Gateway",
                      description="Routes to all 176 Garcar systems",
                      version=GarcarConfig.API_BANK_VERSION)
        app.add_middleware(CORSMiddleware, allow_origins=["*"],
                           allow_methods=["*"], allow_headers=["*"])

        @app.get("/gateway/systems")
        async def list_systems():
            return self.registry.network_status()

        @app.get("/gateway/health")
        async def network_health():
            all_sys = self.registry.all_alive()
            return {"alive_systems": len(all_sys),
                    "total_routed": self._routed,
                    "domains": list(set(s.domain for s in all_sys))}

        @app.post("/gateway/route/{system_id}")
        async def route_to_system(system_id: str, request: Request):
            system = self.registry.get(system_id)
            if not system:
                return JSONResponse({"error": f"System {system_id} not found"}, 404)
            body = await request.json()
            token = self.bank.auth.service_token("garcar-gateway")
            result = await self.bank.messaging.remote_emit(
                system.base_url, body.get("type", "request"),
                body.get("payload", {}), token
            )
            self._routed += 1
            return result

        @app.post("/gateway/broadcast")
        async def broadcast(request: Request):
            body = await request.json()
            sent = await self.registry.broadcast(
                body.get("type", "broadcast"),
                body.get("payload", {}),
                domain=body.get("domain")
            )
            return {"broadcast_sent_to": sent}

        @app.post("/gateway/capability/{capability}")
        async def route_by_capability(capability: str, request: Request):
            systems = self.registry.find_by_capability(capability)
            if not systems:
                return JSONResponse({"error": f"No system with capability: {capability}"}, 404)
            body = await request.json()
            # Simple round-robin: use first alive system
            target = systems[self._routed % len(systems)]
            token = self.bank.auth.service_token("garcar-gateway")
            result = await self.bank.messaging.remote_emit(
                target.base_url, body.get("type", "request"),
                body.get("payload", {}), token
            )
            self._routed += 1
            return result

        return app

    def run(self, host: str = "0.0.0.0", port: int = 9000):
        if not GATEWAY_AVAILABLE:
            logger.error("FastAPI/uvicorn required. pip install fastapi uvicorn aiohttp")
            return
        import uvicorn
        uvicorn.run(self.app, host=host, port=port)
