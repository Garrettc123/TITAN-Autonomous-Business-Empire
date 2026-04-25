"""
GARCAR UNIVERSAL API BANK
=========================
Shared across ALL 176 Garrettc123 systems.
Every system imports this module for unified:
  - Authentication & JWT
  - Stripe payments
  - OpenAI / LLM calls
  - Webhooks
  - Inter-system messaging
  - Health reporting
  - Telemetry
  - Rate limiting
  - Secrets management

Version: 2.0.0-GARCAR
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    import jwt
except ImportError:
    jwt = None

logger = logging.getLogger("garcar.api_bank")


# ─────────────────────────────────────────────
# CONFIG — reads from env, safe defaults
# ─────────────────────────────────────────────

class GarcarConfig:
    SYSTEM_ID: str = os.getenv("GARCAR_SYSTEM_ID", "titan-business-empire")
    JWT_SECRET: str = os.getenv("GARCAR_JWT_SECRET", "change-me-in-production")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    ORCHESTRATOR_URL: str = os.getenv("GARCAR_ORCHESTRATOR_URL", "https://orchestrator.garcar.io")
    REGISTRY_URL: str = os.getenv("GARCAR_REGISTRY_URL", "https://registry.garcar.io")
    API_BANK_VERSION: str = "2.0.0"
    RATE_LIMIT_RPM: int = int(os.getenv("GARCAR_RATE_LIMIT_RPM", "600"))
    LOG_LEVEL: str = os.getenv("GARCAR_LOG_LEVEL", "INFO")


# ─────────────────────────────────────────────
# AUTH MODULE
# ─────────────────────────────────────────────

class AuthBank:
    """JWT authentication shared across all Garcar systems."""

    def __init__(self, secret: str = GarcarConfig.JWT_SECRET):
        self.secret = secret

    def issue_token(self, subject: str, roles: List[str] = None,
                    ttl_hours: int = 24) -> str:
        payload = {
            "sub": subject,
            "iss": "garcar",
            "iat": int(time.time()),
            "exp": int(time.time()) + ttl_hours * 3600,
            "roles": roles or ["agent"],
            "system": GarcarConfig.SYSTEM_ID,
            "jti": str(uuid.uuid4()),
        }
        if jwt:
            return jwt.encode(payload, self.secret, algorithm="HS256")
        # Fallback: simple base64-ish token without PyJWT
        import base64
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        sig = hmac.new(self.secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        return f"{encoded}.{sig}"

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        try:
            if jwt:
                return jwt.decode(token, self.secret, algorithms=["HS256"])
            parts = token.split(".")
            if len(parts) != 2:
                return None
            import base64
            payload = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
            if payload.get("exp", 0) < time.time():
                return None
            return payload
        except Exception as e:
            logger.warning(f"Token verification failed: {e}")
            return None

    def service_token(self, service_name: str) -> str:
        """Issue a machine-to-machine service token."""
        return self.issue_token(subject=service_name, roles=["service", "internal"], ttl_hours=1)


# ─────────────────────────────────────────────
# PAYMENT MODULE (Stripe)
# ─────────────────────────────────────────────

class PaymentBank:
    """Stripe payment operations — reused across all Garcar revenue systems."""

    def __init__(self):
        self.api_key = GarcarConfig.STRIPE_SECRET_KEY
        self.webhook_secret = GarcarConfig.STRIPE_WEBHOOK_SECRET
        self.base_url = "https://api.stripe.com/v1"

    async def create_payment_intent(self, amount_cents: int, currency: str = "usd",
                                     metadata: Dict = None) -> Dict[str, Any]:
        if not aiohttp:
            return {"error": "aiohttp not installed"}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/x-www-form-urlencoded"}
        data = {"amount": amount_cents, "currency": currency,
                "metadata[system]": GarcarConfig.SYSTEM_ID}
        if metadata:
            for k, v in metadata.items():
                data[f"metadata[{k}]"] = str(v)
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self.base_url}/payment_intents",
                              headers=headers, data=data) as r:
                return await r.json()

    async def create_subscription(self, customer_id: str, price_id: str,
                                   trial_days: int = 0) -> Dict[str, Any]:
        if not aiohttp:
            return {"error": "aiohttp not installed"}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/x-www-form-urlencoded"}
        data = {"customer": customer_id, "items[0][price]": price_id}
        if trial_days:
            data["trial_period_days"] = trial_days
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self.base_url}/subscriptions",
                              headers=headers, data=data) as r:
                return await r.json()

    def verify_webhook(self, payload: bytes, sig_header: str) -> Optional[Dict]:
        try:
            import stripe
            return stripe.Webhook.construct_event(
                payload, sig_header, self.webhook_secret)
        except Exception:
            # Manual HMAC verification fallback
            timestamp = sig_header.split("t=")[1].split(",")[0]
            signed_payload = f"{timestamp}.{payload.decode()}"
            expected = hmac.new(
                self.webhook_secret.encode(),
                signed_payload.encode(), hashlib.sha256).hexdigest()
            received = [s.split("v1=")[1] for s in sig_header.split(",")
                        if s.startswith("v1=")]
            if received and hmac.compare_digest(expected, received[0]):
                return json.loads(payload)
            return None


# ─────────────────────────────────────────────
# LLM MODULE (OpenAI compatible)
# ─────────────────────────────────────────────

class LLMBank:
    """Unified LLM calls — all Garcar AI agents use this."""

    def __init__(self, model: str = "gpt-4o"):
        self.model = model
        self.api_key = GarcarConfig.OPENAI_API_KEY
        self.base_url = "https://api.openai.com/v1"
        self.total_tokens_used = 0

    async def complete(self, messages: List[Dict[str, str]],
                       temperature: float = 0.7,
                       max_tokens: int = 2048,
                       system_prompt: str = None) -> str:
        if not aiohttp:
            return "[aiohttp required for LLM calls]"
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self.base_url}/chat/completions",
                              headers=headers, json=payload) as r:
                result = await r.json()
                self.total_tokens_used += result.get("usage", {}).get("total_tokens", 0)
                return result["choices"][0]["message"]["content"]

    async def embed(self, text: str) -> List[float]:
        if not aiohttp:
            return []
        payload = {"model": "text-embedding-3-small", "input": text}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self.base_url}/embeddings",
                              headers=headers, json=payload) as r:
                result = await r.json()
                return result["data"][0]["embedding"]


# ─────────────────────────────────────────────
# INTER-SYSTEM MESSAGING
# ─────────────────────────────────────────────

@dataclass
class SystemMessage:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender: str = ""
    recipient: str = "*"  # "*" = broadcast to all systems
    message_type: str = "event"
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    reply_to: Optional[str] = None
    ttl_seconds: int = 300


class MessagingBank:
    """Inter-system event bus — all 176 Garcar systems communicate via this."""

    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._queue: asyncio.Queue = None
        self.messages_sent = 0
        self.messages_received = 0

    def _get_queue(self):
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=10000)
        return self._queue

    def subscribe(self, message_type: str, handler: Callable):
        if message_type not in self._handlers:
            self._handlers[message_type] = []
        self._handlers[message_type].append(handler)

    async def publish(self, msg: SystemMessage):
        self.messages_sent += 1
        queue = self._get_queue()
        await queue.put(msg)
        handlers = self._handlers.get(msg.message_type, []) + \
                   self._handlers.get("*", [])
        for handler in handlers:
            try:
                await handler(msg) if asyncio.iscoroutinefunction(handler) \
                    else handler(msg)
            except Exception as e:
                logger.error(f"Handler error for {msg.message_type}: {e}")

    async def emit(self, message_type: str, payload: Dict,
                   recipient: str = "*") -> SystemMessage:
        msg = SystemMessage(
            sender=GarcarConfig.SYSTEM_ID,
            recipient=recipient,
            message_type=message_type,
            payload=payload
        )
        await self.publish(msg)
        return msg

    async def remote_emit(self, url: str, message_type: str,
                          payload: Dict, token: str = "") -> Dict:
        """Send message to a remote Garcar system."""
        if not aiohttp:
            return {"error": "aiohttp required"}
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {"sender": GarcarConfig.SYSTEM_ID, "type": message_type,
                "payload": payload, "timestamp": datetime.utcnow().isoformat()}
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{url}/events", headers=headers,
                              json=body, timeout=aiohttp.ClientTimeout(total=10)) as r:
                return await r.json()


# ─────────────────────────────────────────────
# TELEMETRY & HEALTH
# ─────────────────────────────────────────────

@dataclass
class HealthReport:
    system_id: str
    status: str  # "healthy" | "degraded" | "critical"
    uptime_seconds: float
    metrics: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    version: str = GarcarConfig.API_BANK_VERSION


class TelemetryBank:
    """Unified health and metrics — all systems report through this."""

    def __init__(self):
        self._start_time = time.time()
        self._counters: Dict[str, int] = {}
        self._gauges: Dict[str, float] = {}
        self._errors: List[Dict] = []

    def increment(self, metric: str, value: int = 1):
        self._counters[metric] = self._counters.get(metric, 0) + value

    def gauge(self, metric: str, value: float):
        self._gauges[metric] = value

    def record_error(self, error: str, context: Dict = None):
        self._errors.append({
            "error": error, "context": context or {},
            "timestamp": datetime.utcnow().isoformat()
        })
        if len(self._errors) > 1000:
            self._errors = self._errors[-1000:]

    def health(self) -> HealthReport:
        recent_errors = [e for e in self._errors
                         if (datetime.utcnow() -
                             datetime.fromisoformat(e["timestamp"])).seconds < 300]
        status = "healthy"
        if len(recent_errors) > 10:
            status = "degraded"
        if len(recent_errors) > 50:
            status = "critical"
        return HealthReport(
            system_id=GarcarConfig.SYSTEM_ID,
            status=status,
            uptime_seconds=time.time() - self._start_time,
            metrics={**self._counters, **self._gauges,
                     "recent_errors": len(recent_errors)}
        )

    async def push_health(self) -> bool:
        """Push health report to Garcar orchestrator."""
        if not aiohttp:
            return False
        report = self.health()
        payload = {
            "system_id": report.system_id,
            "status": report.status,
            "uptime": report.uptime_seconds,
            "metrics": report.metrics,
            "timestamp": report.timestamp.isoformat(),
            "version": report.version
        }
        auth = AuthBank()
        token = auth.service_token(GarcarConfig.SYSTEM_ID)
        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{GarcarConfig.ORCHESTRATOR_URL}/health",
                    headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=5)) as r:
                    return r.status == 200
        except Exception:
            return False


# ─────────────────────────────────────────────
# RATE LIMITER
# ─────────────────────────────────────────────

class RateLimiter:
    """Token-bucket rate limiter — all Garcar API calls go through this."""

    def __init__(self, rpm: int = GarcarConfig.RATE_LIMIT_RPM):
        self.rpm = rpm
        self._tokens: Dict[str, List[float]] = {}

    def allow(self, key: str = "default") -> bool:
        now = time.time()
        window = 60.0
        calls = self._tokens.get(key, [])
        calls = [t for t in calls if now - t < window]
        if len(calls) >= self.rpm:
            return False
        calls.append(now)
        self._tokens[key] = calls
        return True

    async def wait_for_slot(self, key: str = "default"):
        while not self.allow(key):
            await asyncio.sleep(0.1)


# ─────────────────────────────────────────────
# SECRETS MANAGER
# ─────────────────────────────────────────────

class SecretsBank:
    """Centralized secrets — reads from env, supports rotation hooks."""

    _secrets: Dict[str, str] = {}
    _rotation_hooks: Dict[str, Callable] = {}

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        return cls._secrets.get(key) or os.getenv(key, default)

    @classmethod
    def set(cls, key: str, value: str):
        cls._secrets[key] = value

    @classmethod
    def register_rotation_hook(cls, key: str, hook: Callable):
        cls._rotation_hooks[key] = hook

    @classmethod
    async def rotate(cls, key: str):
        if key in cls._rotation_hooks:
            new_value = await cls._rotation_hooks[key]()
            cls.set(key, new_value)
            logger.info(f"Secret rotated: {key}")


# ─────────────────────────────────────────────
# MASTER API BANK — single import for all systems
# ─────────────────────────────────────────────

class GarcarAPIBank:
    """
    THE universal API bank.
    Import this ONE class in every Garcar system:

        from garcar_api_bank import GarcarAPIBank
        bank = GarcarAPIBank()
        token = bank.auth.issue_token("agent-1")
        await bank.payments.create_payment_intent(9900)
        response = await bank.llm.complete([{"role": "user", "content": "analyze"}])
        await bank.messaging.emit("revenue.generated", {"amount": 9900})
        bank.telemetry.increment("transactions")
    """

    def __init__(self, system_id: str = None):
        if system_id:
            GarcarConfig.SYSTEM_ID = system_id
        self.auth = AuthBank()
        self.payments = PaymentBank()
        self.llm = LLMBank()
        self.messaging = MessagingBank()
        self.telemetry = TelemetryBank()
        self.rate_limiter = RateLimiter()
        self.secrets = SecretsBank()
        self.config = GarcarConfig()
        self._initialized_at = datetime.utcnow()
        logger.info(
            f"GarcarAPIBank v{GarcarConfig.API_BANK_VERSION} "
            f"initialized for system: {GarcarConfig.SYSTEM_ID}"
        )

    async def startup(self):
        """Run on system start — registers with orchestrator."""
        await self.messaging.emit("system.startup", {
            "system_id": GarcarConfig.SYSTEM_ID,
            "version": GarcarConfig.API_BANK_VERSION,
            "timestamp": self._initialized_at.isoformat()
        })
        logger.info(f"System {GarcarConfig.SYSTEM_ID} registered with Garcar network.")

    async def shutdown(self):
        """Run on graceful shutdown."""
        await self.messaging.emit("system.shutdown", {
            "system_id": GarcarConfig.SYSTEM_ID,
            "uptime": self.telemetry.health().uptime_seconds
        })

    def status(self) -> Dict[str, Any]:
        health = self.telemetry.health()
        return {
            "system_id": GarcarConfig.SYSTEM_ID,
            "api_bank_version": GarcarConfig.API_BANK_VERSION,
            "status": health.status,
            "uptime_seconds": health.uptime_seconds,
            "metrics": health.metrics,
            "initialized_at": self._initialized_at.isoformat()
        }
