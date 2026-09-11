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
