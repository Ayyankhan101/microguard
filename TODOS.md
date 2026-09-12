# TODOs

Deferred work with an explicit trigger for picking it up. Items land here from
reviews; each one names the condition that should make someone act on it.

## ~~P2 — Cap the actor-record set by count~~ (done 2026-09-13)

**What:** Bound `mg:v2:actor:*` the way `mg:v1:blocked_ips` is bounded, using
the `ZREMRANGEBYRANK` pattern in `microguard/live/redis_events.py`.

**Why:** Actor records (added by the fingerprint work) use a 30-day sliding TTL
with no cap on how many exist. That is fine under normal traffic, where the
number of distinct browser fingerprints is small and stable. It is not fine
against a bot farm generating novel fingerprints, which creates one key per
hash with nothing evicting them for a month.

This is the same failure class already found and fixed once in this project:
`blocked_ips` grew with the number of distinct attackers until it was capped at
1000. The fix is known and the pattern is already in the codebase.

**Pros:** Removes the last unbounded structure. Gives the signal health panel a
real number to display (actors tracked against a ceiling) instead of an
unbounded count.

**Cons:** A second structure to keep consistent with the records it indexes, and
eviction by recency can drop a slow, patient adversary in favour of noisy
short-lived ones.

**Context:** Deferred deliberately during the 2026-09-12 CEO review (decision
5A). Still open after v3.0.0: the record became a HASH so its counters are
atomic, but nothing caps how many actor keys exist. The TTL alone was judged sufficient for expected traffic, and the cap was
left as a known, triggered follow-up rather than speculative work.

**Effort:** S · **Depends on:** the actor-identity work landing first.

**Trigger:** `mg:v2:actor:*` key count above roughly 50,000, or the health panel
showing sustained growth in novel hashes.

**Done:** `ACTOR_INDEX_KEY` (`mg:v2:actor_index`), a ZSET scored by last-seen
epoch, capped at `MAX_TRACKED_ACTORS = 10_000` via ZREMRANGEBYRANK-equivalent
eviction in `live/fingerprint.py::_evict_surplus_actors`. Index entry and
record are dropped together -- an orphaned index entry would be reported as a
tracked actor by the health panel. Eviction is by recency rather than sightings
(unlike `blocked_ips`, which keeps the busiest): the busiest actor here is
usually the farm. The named cost stands -- a slow, patient adversary can be
pushed out by noisy ones -- and bounded memory is still the better trade.

## ~~P2 — Evaluate CrowdSec in place of spec 0002~~ (evaluated 2026-09-13 — declined)

**Decision: do not adopt CrowdSec. Keep what shipped.**

The item's premise expired. It asked whether to integrate CrowdSec *instead of
building* spec 0002, and set its trigger as "before the signal-seam milestone
starts". That milestone shipped in v3.0.0 (`29aaf79`); `specs/0002` reads
**SHIPPED**. Tor exit lists, AbuseIPDB, and hosting-range resolution all exist
in `live/signals_refresher.py` and `live/abuseipdb.py`, behind the out-of-band
seam in `signals.py`.

So the real question is no longer "build or adopt" but "replace working code
with a dependency", which is a much weaker case:

- **The cost that motivated the item is already paid.** The argument was saving
  several days of work. That work is done, tested, and gated.
- **The risk it named is already designed out.** The concern was "an outbound
  network dependency near the request path". The signal seam moved every lookup
  out of process: `microguard signals` resolves into Redis on its own schedule
  and `live/scorer.py` only ever reads. CrowdSec would add a *daemon* to that
  path, which is strictly more coupling than the HTTP fetches it replaces.
- **It contradicts a standing constraint.** `microguard scan` is deliberately
  zero-dependency beyond micrograd. CrowdSec is a Go daemon plus a bouncer.
  That is a reasonable thing for an operator to run and a bad thing for this
  tool to require.
- **The network effect is still real, and still not ours to have.** CrowdSec's
  crowdsourced reputation genuinely beats public feeds. That remains a good
  reason for an operator to run CrowdSec *alongside* Microguard — which the
  original review already identified as the complementary case.

**What would reopen this:** an operator deployment where CrowdSec is already
running, making integration a read rather than a new dependency. At that point
it is a new signal source behind the existing seam (`KNOWN_SIGNAL_SOURCES` in
`signals.py`), not a replacement, and it inherits the observe-until-promoted
posture for free.

## ~~P3 — labeler.py rule-order debt~~ (addressed 2026-09-13)

**What:** `label_session` in `microguard/labeler.py` is a linear if-chain where
the first matching rule wins, which makes rule *order* part of the semantics.
The file already contains one rule proven unreachable, and the signal work adds
two more `_check_*` calls to the chain.

**Why:** Nothing is broken today. The problem is that the chain gets harder to
reorder or audit with every addition, and an unreachable rule is a symptom of
exactly that. At some point the cost of not being able to safely change it
exceeds the cost of restructuring it.

**Pros:** A rule set with explicit precedence instead of implicit ordering would
be auditable, and would let rules declare confidence without competing for
position in a list.

**Cons:** Genuinely risky work with no user-visible payoff. Every rule's behavior
would need to be pinned by tests before it could move, and the detection
behavior must not change.

**Context:** Surfaced during the 2026-09-12 CEO review, Section 10. Not urgent —
recorded so the next person to find the chain confusing knows it is a known
property, not something they misread.

**Effort:** M · **Depends on:** nothing, but easier after the signal work settles.

**Trigger:** When the chain passes roughly 250 lines, or when a rule genuinely
needs to be reordered.

## ~~P3 — LiveSession and Session only duck-type~~ (addressed 2026-09-13)

**What:** `microguard/live/state.py`'s `LiveSession` and `microguard/features.py`'s
`Session` are two unrelated classes that happen to expose the same attribute names.
`microguard/live/scorer.py:126` carries a `# type: ignore[arg-type]` on the
`label_session` call because of it, and `state.py:23` documents the arrangement as a
convention rather than a contract.

**Why:** The signal-seam work makes this load-bearing. Two new `_check_*` functions will
read from a signals mapping that only the live path ever populates, so the convention now
carries detection logic rather than just matching attribute names. A mismatch used to
mean a type error; it now means a rule silently evaluating differently on the two paths.

There is precedent for exactly that failure: `labeler.py` rule 7 divided by
`session.duration` and behaved correctly on second-granular log timestamps while
extrapolating to six figures per minute on microsecond live timestamps. Same two shapes,
same class of bug.

**Pros:** A shared Protocol both shapes satisfy would let mypy check the call instead of
ignoring it, and would make any future divergence a build failure rather than a runtime
surprise.

**Cons:** Touches the module every detection rule lives in, for a change with no
user-visible effect. Both shapes would need their attribute contracts pinned by tests
before the Protocol could be trusted.

**Context:** Surfaced by the 2026-09-12 eng review of `docs/designs/remaining-epic.md`.
Deliberately not folded into that plan, which is already about 19 days of work. Start at
`scorer.py:126` and `state.py:20-40`.

**Effort:** M · **Depends on:** nothing, but cheapest right after the signal seam lands.

**Trigger:** When a detection rule next behaves differently between `microguard scan`
and the live path, or when a third session shape appears.
