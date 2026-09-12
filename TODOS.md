# TODOs

Deferred work with an explicit trigger for picking it up. Items land here from
reviews; each one names the condition that should make someone act on it.

## P2 — Cap the actor-record set by count

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

## P2 — Evaluate CrowdSec in place of spec 0002

**What:** Compare integrating CrowdSec against building the threat-intel feed
work described in `specs/0002-threat-intel-feeds.md`.

**Why:** CrowdSec is open source, self-hosted, and already provides crowdsourced
IP reputation shared across its community — the network effect this project
explicitly gave up on in the epic's Out of Scope. Spec 0002 builds a weaker
version of the same capability from public feeds plus one keyed source.

The 2026-09-12 CEO review kept 0002 in scope by decision, but flagged it as the
cut candidate if the plan needs to shrink. Confirming that with an actual
comparison beats carrying the assumption.

**Pros:** Could remove several days of work and the only component that puts an
outbound network dependency anywhere near the request path. CrowdSec's reputation
data is strictly better than what public feeds alone provide.

**Cons:** Adds a substantial external dependency to a tool whose CLI mode is
deliberately zero-dependency, and integration work is not free either.

**Context:** The relevant distinction found during the review: CrowdSec is strong
at known-bad IPs and weak against a tuned headless browser on clean residential
proxies. That second half is what the fingerprinting work targets, so the two are
complementary rather than alternatives — but only 0002 overlaps.

**Effort:** S to evaluate · **Depends on:** nothing.

**Trigger:** Before the signal-seam milestone starts, or any time the plan needs
to shrink.

## P3 — labeler.py rule-order debt

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

## P3 — LiveSession and Session only duck-type

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
