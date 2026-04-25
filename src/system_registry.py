"""
GARCAR SYSTEM REGISTRY
=======================
Universal discovery layer — all 176 Garcar systems
register here so they can find and converse with each other.

Every system can:
  - Register itself with capabilities
  - Discover other systems by capability or domain
  - Send typed messages to any registered system
  - Subscribe to capability events
  - Check system health network-wide
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from garcar_api_bank import GarcarAPIBank, GarcarConfig, SystemMessage

logger = logging.getLogger("garcar.registry")


@dataclass
class SystemCapability:
    name: str
    version: str = "1.0.0"
    description: str = ""
    endpoints: List[str] = field(default_factory=list)
    input_schema: Dict = field(default_factory=dict)
    output_schema: Dict = field(default_factory=dict)


@dataclass
class RegisteredSystem:
    system_id: str
    name: str
    domain: str  # e.g. "finance", "healthcare", "legal", "realestate"
    capabilities: List[SystemCapability]
    api_bank_version: str
    base_url: str
    status: str = "active"  # active | degraded | offline
    registered_at: datetime = field(default_factory=datetime.utcnow)
    last_heartbeat: datetime = field(default_factory=datetime.utcnow)
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_alive(self) -> bool:
        return (datetime.utcnow() - self.last_heartbeat) < timedelta(minutes=5)

    def capability_names(self) -> List[str]:
        return [c.name for c in self.capabilities]


class GarcarSystemRegistry:
    """
    In-process registry for all Garcar systems.
    In production, back this with Redis or a database.
    """

    def __init__(self, bank: GarcarAPIBank = None):
        self.bank = bank or GarcarAPIBank()
        self._systems: Dict[str, RegisteredSystem] = {}
        self._capability_index: Dict[str, List[str]] = {}  # capability -> [system_ids]
        self._domain_index: Dict[str, List[str]] = {}      # domain -> [system_ids]
        self._event_hooks: Dict[str, List[Callable]] = {}

    # ── Registration ──────────────────────────

    def register(self, system: RegisteredSystem):
        self._systems[system.system_id] = system
        # Index by capability
        for cap in system.capabilities:
            if cap.name not in self._capability_index:
                self._capability_index[cap.name] = []
            if system.system_id not in self._capability_index[cap.name]:
                self._capability_index[cap.name].append(system.system_id)
        # Index by domain
        if system.domain not in self._domain_index:
            self._domain_index[system.domain] = []
        if system.system_id not in self._domain_index[system.domain]:
            self._domain_index[system.domain].append(system.system_id)
        logger.info(f"Registered system: {system.system_id} [{system.domain}] "
                    f"capabilities={system.capability_names()}")
        asyncio.ensure_future(self._notify_hooks("system.registered", system))

    def deregister(self, system_id: str):
        system = self._systems.pop(system_id, None)
        if system:
            for cap in system.capabilities:
                if cap.name in self._capability_index:
                    self._capability_index[cap.name].remove(system_id)
            logger.info(f"Deregistered system: {system_id}")

    def heartbeat(self, system_id: str, status: str = "active"):
        if system_id in self._systems:
            self._systems[system_id].last_heartbeat = datetime.utcnow()
            self._systems[system_id].status = status

    # ── Discovery ─────────────────────────────

    def find_by_capability(self, capability: str) -> List[RegisteredSystem]:
        ids = self._capability_index.get(capability, [])
        return [self._systems[i] for i in ids
                if i in self._systems and self._systems[i].is_alive]

    def find_by_domain(self, domain: str) -> List[RegisteredSystem]:
        ids = self._domain_index.get(domain, [])
        return [self._systems[i] for i in ids
                if i in self._systems and self._systems[i].is_alive]

    def find_by_tag(self, tag: str) -> List[RegisteredSystem]:
        return [s for s in self._systems.values()
                if tag in s.tags and s.is_alive]

    def get(self, system_id: str) -> Optional[RegisteredSystem]:
        return self._systems.get(system_id)

    def all_alive(self) -> List[RegisteredSystem]:
        return [s for s in self._systems.values() if s.is_alive]

    def network_status(self) -> Dict[str, Any]:
        all_sys = list(self._systems.values())
        alive = [s for s in all_sys if s.is_alive]
        return {
            "total_registered": len(all_sys),
            "alive": len(alive),
            "offline": len(all_sys) - len(alive),
            "domains": list(self._domain_index.keys()),
            "capabilities": list(self._capability_index.keys()),
            "systems": [{"id": s.system_id, "domain": s.domain,
                          "status": s.status, "alive": s.is_alive}
                         for s in all_sys]
        }

    # ── Messaging ─────────────────────────────

    async def message_system(self, target_id: str,
                              message_type: str,
                              payload: Dict) -> Optional[Dict]:
        system = self.get(target_id)
        if not system:
            logger.warning(f"System {target_id} not found in registry")
            return None
        if not system.base_url:
            # In-process delivery via messaging bank
            msg = SystemMessage(
                sender=GarcarConfig.SYSTEM_ID,
                recipient=target_id,
                message_type=message_type,
                payload=payload
            )
            await self.bank.messaging.publish(msg)
            return {"delivered": True, "method": "in-process"}
        # Remote delivery
        token = self.bank.auth.service_token(GarcarConfig.SYSTEM_ID)
        return await self.bank.messaging.remote_emit(
            system.base_url, message_type, payload, token)

    async def broadcast(self, message_type: str, payload: Dict,
                         domain: str = None) -> int:
        """Broadcast to all alive systems, optionally filtered by domain."""
        targets = self.find_by_domain(domain) if domain else self.all_alive()
        sent = 0
        for system in targets:
            if system.system_id == GarcarConfig.SYSTEM_ID:
                continue
            try:
                await self.message_system(system.system_id, message_type, payload)
                sent += 1
            except Exception as e:
                logger.error(f"Broadcast to {system.system_id} failed: {e}")
        return sent

    # ── Event hooks ───────────────────────────

    def on(self, event: str, hook: Callable):
        if event not in self._event_hooks:
            self._event_hooks[event] = []
        self._event_hooks[event].append(hook)

    async def _notify_hooks(self, event: str, data: Any):
        for hook in self._event_hooks.get(event, []):
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(data)
                else:
                    hook(data)
            except Exception as e:
                logger.error(f"Hook error for {event}: {e}")


# ── Global singleton registry ──────────────────

_global_registry: Optional[GarcarSystemRegistry] = None


def get_registry(bank: GarcarAPIBank = None) -> GarcarSystemRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = GarcarSystemRegistry(bank)
    return _global_registry


def register_this_system(name: str, domain: str,
                          capabilities: List[str],
                          base_url: str = "",
                          tags: List[str] = None,
                          bank: GarcarAPIBank = None) -> RegisteredSystem:
    """One-line self-registration helper for every Garcar system."""
    registry = get_registry(bank)
    caps = [SystemCapability(name=c) for c in capabilities]
    system = RegisteredSystem(
        system_id=GarcarConfig.SYSTEM_ID,
        name=name,
        domain=domain,
        capabilities=caps,
        api_bank_version=GarcarConfig.API_BANK_VERSION,
        base_url=base_url,
        tags=tags or []
    )
    registry.register(system)
    return system
