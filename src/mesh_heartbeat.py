"""Opt-in, signed, minimal health reporter for GARCAR Sync Core.

This component has NO remote command reception or privileged operations.
Do not interpret a heartbeat as proof of payments, agents, or cross-node delivery.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
from urllib.parse import urlsplit
from urllib.request import (HTTPRedirectHandler, HTTPSHandler,
                            OpenerDirector, ProxyHandler, Request, build_opener)


NODE_ID = "titan-business-empire"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _endpoint(origin):
    url = urlsplit(origin or "")
    host = (url.hostname or "").lower().rstrip(".")
    if url.scheme != "https" or not host:
        raise ValueError("only configured HTTPS Sync Core origins are accepted")
    if url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        raise ValueError("Sync Core URL must be a plain origin")
    if url.port not in (None, 443):
        raise ValueError("nonstandard Sync Core ports are not allowed")
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError("local destinations are forbidden")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError("private/reserved destinations are forbidden")
    except ValueError as e:
        # Private addresses deliberately cause a separate ValueError above.
        if str(e) == "private/reserved destinations are forbidden":
            raise
    return "https://" + host + "/mesh/ingest"


def send_heartbeat(status="degraded", environ=None):
    env = os.environ if environ is None else environ
    if env.get("GARCAR_MESH_PUBLISH_ENABLED", "").lower() != "true":
        return {"state": "disabled", "node": NODE_ID}
    if status not in {"healthy", "degraded", "offline"}:
        raise ValueError("invalid health state")
    key = env.get("GARCAR_MESH_NODE_KEY", "")
    if len(key.encode("utf-8")) < 32:
        raise RuntimeError("mesh signing key unavailable")
    endpoint = _endpoint(env.get("GARCAR_SYNC_CORE_ORIGIN", ""))
    revision = env.get("GARCAR_RELEASE_SHA", "unverified")[:64]
    if not re.fullmatch(r"[a-zA-Z0-9._-]{1,64}", revision):
        revision = "unverified"
    event = {
        "event_id": "mesh-hb-" + secrets.token_urlsafe(18),
        "source": NODE_ID,
        "type": "mesh.heartbeat.v1",
        "payload": {"status": status, "revision": revision},
    }
    body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = hmac.new(key.encode("utf-8"), timestamp.encode() + b"." + body,
                         hashlib.sha256).hexdigest()
    request = Request(endpoint, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-Mesh-Node": NODE_ID,
        "X-Mesh-Time": timestamp,
        "X-Mesh-Signature": signature,
    })
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler())
    with opener.open(request, timeout=4) as response:
        if response.status != 200:
            raise RuntimeError("mesh heartbeat not acknowledged")
        data = json.loads(response.read(2048))
    if data.get("event_id") != event["event_id"] or data.get("signed_node") != NODE_ID:
        raise RuntimeError("mesh acknowledgment mismatch")
    if data.get("action_authority") != "none":
        raise RuntimeError("mesh response requested unauthorized action")
    return {"state": "acknowledged", "node": NODE_ID, "event_id": event["event_id"],
            "sync_delivery_verified": False}


if __name__ == "__main__":
    # Explicit one-shot operation; no automatic sending unless enabled in environment.
    print(json.dumps(send_heartbeat()))
