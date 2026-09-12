"""Fingerprint storage — binding a browser hash to an actor.

    POST /fp ──> record_fingerprint()
                   │
                   ├─ EXISTS live:v2:{ip}          the 7A binding gate
                   ├─ SET NX mg:v1:fp:{ip}         claim the binding, atomically
                   ├─ SADD   mg:v1:fp_ips:{hash}   distinct IPs in the window
                   ├─ HINCRBY mg:v2:actor:{hash}   durable, sliding TTL
                   └─ SET    mg:v1:fp:{ip}         + the denormalized count

Why the binding is claimed with SET NX rather than checked and then written:
a GET followed by a SET is a race, and it is a race an attacker can win on
purpose. Two concurrent submissions from one bound IP carrying different
hashes both read "nothing bound", both pass the gate, and both record -- which
lets one IP with a live session credit an unlimited number of harvested hashes
and inflate the cross-IP count for each. That is precisely the amplification
the binding rule exists to prevent, so the claim has to be atomic.

Why the count is denormalized here rather than read at scoring time: the count
lives under the hash, and the hash is only known after reading the actor's own
record, so the second read depends on the first and cannot be pipelined. `/fp`
runs once per page load and already knows both, so it writes the count where
`/check` can get it in the pipeline it was already running.

Why fingerprint state is its own key rather than a field on `mg:v1:signals:{ip}`:
the refresher SETs that key wholesale on every pass, so two writers would
clobber each other. `/check` reads both in one pipeline, so the split costs
nothing on the request path.

The actor record is a HASH rather than a JSON string so sightings increment
with HINCRBY instead of a read-modify-write, which lost increments under
exactly the traffic the number is for. The prefix carries a version because
HINCRBY against a v1 string is WRONGTYPE -- the same trap the session store
already hit once.

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
ACTOR_PREFIX = "mg:v2:actor:"

# SHA-256, hex. The client hashes before sending, so the server never receives
# a raw fingerprint component -- see the privacy note in the README.
_HASH_RE = re.compile(r"\A[0-9a-fA-F]{64}\Z")

# How long distinct IPs sharing one hash stay counted together. A bot farm
# rotating proxies does it fast; a household behind one NAT does not.
DEFAULT_WINDOW_S = 600
# Actor records are the one structure here that outlives a session. Sliding, so
# an actor that stops appearing ages out with no sweeper.
DEFAULT_ACTOR_TTL_S = 30 * 24 * 3600

# Index of every actor record, scored by last-seen epoch. The records
# themselves are individually TTL'd but nothing bounded HOW MANY existed: a
# farm generating novel fingerprints creates one key per hash with nothing
# evicting them for a month. Same failure class as mg:v1:blocked_ips, which
# grew with the number of distinct attackers until it was capped.
ACTOR_INDEX_KEY = "mg:v2:actor_index"

# Evicted by RECENCY, not by sightings. blocked_ips keeps the busiest because
# a repeat blocker is the interesting one there; here the busiest actor is
# usually the farm, and the record worth keeping is whichever was seen last.
# The cost is real and worth naming: a slow, patient adversary can be pushed
# out by noisy short-lived ones. Bounded memory is still the better trade --
# an unbounded set degrades everything.
MAX_TRACKED_ACTORS = 10_000
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
    claim = json.dumps({"hash": fingerprint_hash, "shared_ips": 0})

    # Claim atomically. A GET-then-SET here is a race an attacker can win on
    # purpose: two concurrent submissions with different hashes both see
    # "nothing bound" and both proceed.
    if not client.set(fp_key, claim, ex=fp_ttl, nx=True):
        bound = _bound_hash(client, fp_key)
        if bound is None:
            # An unreadable record must not lock this actor out of ever
            # binding again. Overwrite it and take the claim.
            client.set(fp_key, claim, ex=fp_ttl)
        elif bound != fingerprint_hash:
            return RejectReason.ALREADY_BOUND
        else:
            # A page that loaded twice, or a keepalive POST that retried.
            # Normal traffic, and the IP is already in the set.
            return None

    ips_key = f"{FP_IPS_PREFIX}{fingerprint_hash}"
    pipe = client.pipeline()
    pipe.sadd(ips_key, ip)
    pipe.expire(ips_key, window)
    pipe.scard(ips_key)
    *_, shared = pipe.execute()

    _touch_actor(client, fingerprint_hash, int(shared), actor_ttl)
    # Rewritten with the count now that it is known. The claim above could not
    # carry it: the count is only knowable after the SADD, and the SADD must
    # not happen until the claim is won.
    client.set(
        fp_key,
        json.dumps({"hash": fingerprint_hash, "shared_ips": int(shared)}),
        ex=fp_ttl,
    )
    return None


def _bound_hash(client: redis.Redis, fp_key: str) -> str | None:
    """The hash currently bound to this actor, or None if unreadable."""
    raw = cast("str | None", client.get(fp_key))
    if not raw:
        return None
    try:
        value = json.loads(raw).get("hash")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _touch_actor(
    client: redis.Redis, fingerprint_hash: str, distinct_ips: int, ttl: int
) -> None:
    """Record or refresh the durable actor record, atomically.

    A Redis HASH, not a JSON string. The previous read-modify-write lost
    increments under concurrency, which is exactly the traffic these numbers
    describe: a farm hitting one fingerprint from many addresses at once.

    `first_seen` is written with HSETNX, so it is set once and never moves --
    it is the field that makes a returning adversary distinguishable from a new
    one, which is the whole reason a fingerprint is worth more than an IP.
    """
    key = f"{ACTOR_PREFIX}{fingerprint_hash}"
    now = time.time()
    try:
        _actor_pipeline(client, key, now, distinct_ips, ttl)
    except redis.ResponseError:
        # Something non-hash is sitting at a v2 actor key. Nothing in this
        # package writes one, so this means external interference -- but losing
        # an actor permanently over one bad value is the worse outcome.
        logger.warning("actor key %s held the wrong type, replacing it", key)
        client.delete(key)
        _actor_pipeline(client, key, now, distinct_ips, ttl)


def _actor_pipeline(
    client: redis.Redis, key: str, now: float, distinct_ips: int, ttl: int
) -> None:
    pipe = client.pipeline()
    pipe.hsetnx(key, "first_seen", str(now))
    pipe.hset(key, mapping={"last_seen": str(now), "distinct_ips": distinct_ips})
    pipe.hincrby(key, "sightings", 1)
    # Sliding, so an actor that stops appearing ages out with no sweeper.
    pipe.expire(key, ttl)
    # ZADD re-scores an existing member rather than adding a second, so a
    # returning actor moves up the index instead of being evicted by its own
    # return. The index outlives no record: same TTL, refreshed on every touch.
    pipe.zadd(ACTOR_INDEX_KEY, {key.removeprefix(ACTOR_PREFIX): now})
    pipe.expire(ACTOR_INDEX_KEY, ttl)
    pipe.execute()
    _evict_surplus_actors(client)


def _evict_surplus_actors(client: redis.Redis) -> None:
    """Drop the least recently seen actors above the ceiling.

    The index entry and the record it points at are removed together. An index
    entry whose hash is gone would be reported as a tracked actor by the signal
    health panel, which is a lie; a record with no index entry would never be
    evicted at all.

    Never raises: this runs on the /fp path, and losing an eviction is a memory
    cost while raising is a failed request.
    """
    try:
        tracked = cast("int", client.zcard(ACTOR_INDEX_KEY))
        surplus = tracked - MAX_TRACKED_ACTORS
        if surplus <= 0:
            return
        doomed = cast("list[str]", client.zrange(ACTOR_INDEX_KEY, 0, surplus - 1))
        if not doomed:
            return
        pipe = client.pipeline()
        pipe.zrem(ACTOR_INDEX_KEY, *doomed)
        for member in doomed:
            pipe.delete(f"{ACTOR_PREFIX}{member}")
        pipe.execute()
    except redis.RedisError:
        logger.warning("actor index eviction failed, continuing", exc_info=True)
