"""External signals about one actor — the seam between lookups and rules.

`labeler.py` is a pure function of its arguments and must stay that way. It is
called inline on every nginx `auth_request` (`live/scorer.py`), so a lookup
performed *inside* a rule would put a network or Redis round trip in front of a
real visitor, where nginx turns slowness into a 500. Signals are therefore
resolved BEFORE `label_session` and passed in.

This module is deliberately NOT under live/: `live/__init__.py` raises
ImportError when redis-py is absent, and `labeler.py` is on the `microguard
scan` path, which must work on a base install. The Redis-backed resolver lives
in `live/signals_store.py`. Same split, same reason, as `events.py` versus
`live/redis_events.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Every source a deployment can promote out of observe-only. Closed on purpose:
# a typo would leave an operator believing a signal is enforced while it quietly
# is not, which is worse than the signal being off.
#
# `hosting` is deliberately NOT here. It is resolved and recorded on every
# decision, but no rule reads it: a datacenter IP is weak evidence on its own,
# and spec 0002 left the combination rule open because choosing it needs real
# traffic rather than a guess. Listing it as promotable would let an operator
# tick a box, see an "enforced" badge, and get no enforcement at all -- a
# control that silently does nothing is worse than an absent one. Add it here
# in the same change that adds the rule.
#
# Lives here rather than in live/runtime_config.py because the dashboard reads
# it to render its promotion control, and the dashboard must run without
# redis-py -- live/__init__.py raises ImportError without it.
KNOWN_SIGNAL_SOURCES = frozenset({"tor", "abuseipdb", "fingerprint"})


@dataclass(frozen=True)
class Signals:
    """What the slow tier knew about one actor when scoring began.

    `resolved` is the field that matters most. It separates "we looked and
    found nothing" from "we never looked", and those are opposite evidence.
    In a batch `microguard scan` nothing is ever resolved — there is no live
    lookup on that path — so a rule that read an absent fingerprint as proof of
    automation would label every session in every log file a bot. Every rule
    reading this object must check `resolved` first, and `is_promoted` does it
    for you.

    `promoted` carries the deployment's opt-in list. A signal that is resolved
    but not promoted still travels into the recorded decision, so an operator
    can see what it would have done, but it cannot decide a verdict. That is
    observe-until-promoted, expressed as an argument rather than a global, so
    it stays testable and `label_session` stays pure.
    """

    resolved: bool = False
    tor_exit: bool = False
    hosting_range: bool = False
    abuse_score: float | None = None
    # Whether the fingerprint pipeline was consulted at all. Tracked separately
    # from `resolved` because the two are produced by different writers: the
    # refresher writes reputation data on its own schedule, while the
    # fingerprint record is read on every scored request. A rule that infers
    # automation from an ABSENT fingerprint needs to know the difference
    # between "read, and there was none" and "never read".
    fp_resolved: bool = False
    fingerprint_hash: str | None = None
    # Distinct IPs seen sharing this hash inside the window. Denormalized at
    # submission time: the count lives under the hash, and the hash is only
    # known after reading the actor's own record, so resolving it at scoring
    # time would be a dependent second round trip that cannot be pipelined.
    shared_hash_ips: int = 0
    promoted: frozenset[str] = field(default_factory=frozenset)

    def is_promoted(self, source: str) -> bool:
        """Whether `source` may decide a verdict on its own.

        False for anything unresolved, regardless of the promotion list: a
        source cannot act on data that was never looked up. Which flag counts
        as "resolved" depends on the source, since reputation data and
        fingerprints arrive from different writers on different schedules.
        """
        resolved = self.fp_resolved if source == "fingerprint" else self.resolved
        return resolved and source in self.promoted


# The default every batch caller gets. Shared and immutable, so passing it
# costs nothing and no caller can mutate another's view.
EMPTY_SIGNALS = Signals()


# Fields the stored payload may carry. Anything else in it is ignored rather
# than raising: a newer refresher writing a field this reader does not know
# about must not take down the request path it sits on.
_PAYLOAD_FIELDS = (
    "tor_exit",
    "hosting_range",
    "abuse_score",
)


def signals_from_payload(payload: object) -> Signals:
    """Build resolved Signals from a decoded storage payload.

    One parser, used by every reader, so the stored shape has a single
    definition. A payload that is not a mapping returns EMPTY_SIGNALS —
    unresolved, because nothing usable was read.
    """
    if not isinstance(payload, dict):
        return EMPTY_SIGNALS
    known = {k: payload[k] for k in _PAYLOAD_FIELDS if k in payload}
    return Signals(resolved=True, **known)
