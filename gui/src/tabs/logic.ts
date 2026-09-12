/**
 * The arithmetic behind the tabs, kept out of the components so it can be
 * checked against hand-worked numbers rather than through a rendered table.
 */
import type { Decision, ScanSession } from '../api/schemas';

export interface ThresholdCounts {
  bots: number;
  humans: number;
  integrations: number;
  total: number;
  botRate: number;
}

/**
 * Re-slice a finished scan at a different threshold.
 *
 * `>=`, matching cli.py:133 — the scan report and this table must not disagree
 * about a session sitting exactly on the bar. (The live path uses a strict `>`;
 * that difference is in the engine, not here.)
 */
export function countAtThreshold(
  sessions: ScanSession[],
  threshold: number,
): ThresholdCounts {
  let bots = 0;
  let integrations = 0;

  for (const session of sessions) {
    // Recognized integrations are never scored against the bar
    // (live/scorer.py:122), so dragging it must not turn one into a bot.
    if (session.label === 'automated-integration') integrations += 1;
    else if (session.score >= threshold) bots += 1;
  }

  const total = sessions.length;
  return {
    bots,
    integrations,
    humans: total - bots - integrations,
    total,
    botRate: total > 0 ? bots / total : 0,
  };
}

/**
 * How many of the last `window` decisions were allowed through unscored.
 *
 * Fail-open is load-bearing — an unscored request beats an outage — but it
 * means the site is unprotected while it lasts, and the docs are explicit that
 * it should be alerted on rather than treated as noise.
 */
export function failOpenCount(decisions: Decision[], window = 20): number {
  return decisions
    .slice(0, window)
    .filter((decision) => decision.heuristic_reason === 'scoring unavailable').length;
}

export interface FeatureInfluence {
  name: string;
  total: number;
}

/**
 * How much the network can react to each input, ranked.
 *
 * micrograd's MLP.parameters() lays each neuron out as its weights followed by
 * its bias, so the first layer occupies hidden * (inputs + 1) entries and the
 * bias is skipped: it belongs to the neuron, not to any input. Absolute values
 * because this measures reach, not direction.
 */
export function featureInfluence(
  names: string[],
  weights: number[],
  hidden: number,
): FeatureInfluence[] {
  const inputs = names.length;
  const influence = names.map((name, index) => {
    let total = 0;
    for (let neuron = 0; neuron < hidden; neuron += 1) {
      total += Math.abs(weights[neuron * (inputs + 1) + index] ?? 0);
    }
    return { name, total };
  });

  return influence.sort((a, b) => b.total - a.total);
}

/**
 * A clean sweep on an evaluation set is a warning, not a win.
 *
 * The README is explicit that the real-human baseline has low feature
 * diversity — only 9 of the 19 features vary in the training data — so a
 * perfect separation usually says the set is easy, not that the model is.
 * Presenting 1.000 without that context would oversell it.
 */
export function suspiciouslyPerfect(confusion: {
  tp: number;
  fp: number;
  fn: number;
  tn: number;
}): boolean {
  const bots = confusion.tp + confusion.fn;
  const humans = confusion.fp + confusion.tn;
  if (bots === 0 || humans === 0) return false;
  return confusion.fp === 0 && confusion.fn === 0;
}

/**
 * How many of these decisions a candidate threshold would have blocked.
 *
 * The comparison is strict `>`, matching live/scorer.py: a score sitting
 * exactly on the bar has not cleared it, which is what makes a threshold of
 * 1.00 a real never-block setting rather than one that still blocks a
 * saturated score.
 *
 * Computed from decisions already on screen rather than asked of the server,
 * because every decision already carries the score it was judged on. That also
 * means it answers instantly as the slider moves.
 */
export function wouldBlock(scores: number[], threshold: number): number {
  return scores.filter((score) => score > threshold).length;
}

/**
 * What changing the threshold to `candidate` would cost or save, against what
 * actually happened.
 *
 * `delta` is the number that matters: an operator moving a slider wants to
 * know how many MORE visitors get a 403, not the absolute count.
 */
export function thresholdImpact(
  decisions: { score: number; label: string }[],
  candidate: number,
): { actual: number; projected: number; delta: number; sampled: number } {
  const actual = decisions.filter((d) => d.label === 'bot').length;
  const projected = wouldBlock(decisions.map((d) => d.score), candidate);
  return { actual, projected, delta: projected - actual, sampled: decisions.length };
}
