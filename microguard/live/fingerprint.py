"""Fingerprint storage — binding a browser hash to an actor.

    POST /fp ──> record_fingerprint()
                   │
                   ├─ EXISTS live:v2:{ip}          the 7A binding gate
                   ├─ GET    mg:v1:fp:{ip}         first hash per session wins
                   ├─ SADD   mg:v1:fp_ips:{hash}   distinct IPs in the window
                   ├─ SET    mg:v1:actor:{hash}    durable, sliding TTL
                   └─ SET    mg:v1:fp:{ip}         hash + denormalized count

Why the count is denormalized here rather than read at scoring time: the count
lives under the hash, and the hash is only known after reading the actor's own
record, so the second read depends on the first and cannot be pipelined. `/fp`
runs once per page load and already knows both, so it writes the count where
`/check` can get it in the pipeline it was already running.

Why fingerprint state is its own key rather than a field on `mg:v1:signals:{ip}`:
the refresher SETs that key wholesale on every pass, so two writers would
clobber each other. `/check` reads both in one pipeline, so the split costs
nothing on the request path.

Why a hash is only accepted from an actor with session history: `/fp` is public
and unauthenticated, and the cross-IP rule it feeds blocks OTHER people. An
attacker who harvests a hash that real browsers produce -- canvas fingerprints
collide heavily across identical hardware and browser versions -- could replay
it from a botnet and get those real users flagged. Binding to an existing
session limits an attacker to IPs they already control and have generated
traffic from, which is exactly the population the rule is meant to catch.
"""

from __future__ import annotations

import json
import logging
import re
import time
from enum import Enum
from typing import cast

import redis

logger = logging.getLogger(__name__)

FP_PREFIX = "mg:v1:fp:"
FP_IPS_PREFIX = "mg:v1:fp_ips:"
ACTOR_PREFIX = "mg:v1:actor:"

# SHA-256, hex. The client hashes before sending, so the server never receives
# a raw fingerprint component -- see the privacy note in the README.
_HASH_RE = re.compile(r"\A[0-9a-fA-F]{64}\Z")

# How long distinct IPs sharing one hash stay counted together. A bot farm
# rotating proxies does it fast; a household behind one NAT does not.
DEFAULT_WINDOW_S = 600
# Actor records are the one structure here that outlives a session. Sliding, so
# an actor that stops appearing ages out with no sweeper.
DEFAULT_ACTOR_TTL_S = 30 * 24 * 3600
DEFAULT_FP_TTL_S = 1800


class RejectReason(Enum):
    """Why a submission was not recorded. Never surfaced to the caller of /fp.

    The endpoint answers 200 either way: telling an unauthenticated client
    which of its attempts bound successfully hands an attacker the oracle it
    needs to find an unbound IP.
    """

    BAD_HASH = "bad hash"
    NO_SESSION = "no session history for this actor"
    ALREADY_BOUND = "this session already has a fingerprint"


def is_valid_hash(value: str) -> bool:
    """Whether this is a SHA-256 hex digest, and nothing else."""
    return bool(_HASH_RE.match(value or ""))


def record_fingerprint(
    client: redis.Redis,
    ip: str,
    fingerprint_hash: str,
    window: int = DEFAULT_WINDOW_S,
    actor_ttl: int = DEFAULT_ACTOR_TTL_S,
    fp_ttl: int = DEFAULT_FP_TTL_S,
) -> RejectReason | None:
    """Bind a fingerprint to this actor. Returns None on success.

    Raises nothing the caller has to handle for a rejected submission; a
    genuine Redis failure propagates so the route can answer 200 and log it.
    """
    if not is_valid_hash(fingerprint_hash):
        return RejectReason.BAD_HASH

    from .redis_store import SESSION_PREFIX_DEFAULT

    if not client.exists(f"{SESSION_PREFIX_DEFAULT}{ip}"):
        return RejectReason.NO_SESSION

    fp_key = f"{FP_PREFIX}{ip}"
    existing = cast("str | None", client.get(fp_key))
    if existing:
        try:
            bound = json.loads(existing).get("hash")
        except (json.JSONDecodeError, AttributeError):
            bound = None
        if bound and bound != fingerprint_hash:
            return RejectReason.ALREADY_BOUND
        if bound == fingerprint_hash:
            # A page that loads twice, or a keepalive POST that retried. Normal
            # traffic, not an attack, and re-adding is a no-op anyway.
            return None

    ips_key = f"{FP_IPS_PREFIX}{fingerprint_hash}"
    pipe = client.pipeline()
    pipe.sadd(ips_key, ip)
    pipe.expire(ips_key, window)
    pipe.scard(ips_key)
    *_, shared = pipe.execute()

    _touch_actor(client, fingerprint_hash, shared, actor_ttl)
    client.set(
        fp_key,
        json.dumps({"hash": fingerprint_hash, "shared_ips": int(shared)}),
        ex=fp_ttl,
    )
    return None


def _touch_actor(client: redis.Redis, fingerprint_hash: str, distinct_ips: int, ttl: int) -> None:
    """Record or refresh the durable actor record.

    `first_seen` never moves: it is the one field that makes a returning
    adversary distinguishable from a new one, which is the whole reason a
    fingerprint is worth more than an IP.
    """
    key = f"{ACTOR_PREFIX}{fingerprint_hash}"
    now = time.time()
    raw = cast("str | None", client.get(key))
    record = None
    if raw:
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            # A record we cannot read is a record we replace. Refusing to write
            # would leave the actor permanently untracked over one bad value.
            logger.warning("actor record for %s was unreadable, replacing", key)
            record = None
    if not isinstance(record, dict):
        record = {"first_seen": now, "sightings": 0}

    record["last_seen"] = now
    record["sightings"] = int(record.get("sightings", 0)) + 1
    record["distinct_ips"] = int(distinct_ips)
    client.set(key, json.dumps(record), ex=ttl)
