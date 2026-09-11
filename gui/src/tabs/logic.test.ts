import { describe, expect, it } from 'vitest';

import {
  countAtThreshold,
  failOpenCount,
  featureInfluence,
  suspiciouslyPerfect,
} from './logic';
import type { Decision, ScanSession } from '../api/schemas';

function session(overrides: Partial<ScanSession>): ScanSession {
  return {
    ip: '10.0.0.1',
    score: 0.2,
    model_score: 0.1,
    heuristic_label: 'human',
    heuristic_confidence: 0.5,
    heuristic_reason: 'no strong signals either way',
    label: 'human',
    request_count: 1,
    duration: 1,
    top_endpoint: '/',
    user_agent: 'Mozilla/5.0',
    features: {},
    ...overrides,
  };
}

function decision(overrides: Partial<Decision>): Decision {
  return {
    ip: '10.0.0.1',
    label: 'human',
    score: 0.1,
    model_score: 0.1,
    heuristic_label: 'human',
    heuristic_confidence: 0.5,
    heuristic_reason: 'no strong signals either way',
    reason: 'no strong signals either way',
    request_count: 1,
    duration: 0,
    model_loaded: true,
    block_threshold: 0.85,
    ts: 1,
    ...overrides,
  };
}

describe('countAtThreshold', () => {
  it('counts sessions at or above the threshold as bots, matching the CLI', () => {
    const counts = countAtThreshold(
      [session({ score: 0.7 }), session({ score: 0.69 })],
      0.7,
    );

    expect(counts.bots).toBe(1);
    expect(counts.humans).toBe(1);
  });

  it('never counts an automated integration as a bot, however low the bar', () => {
    const counts = countAtThreshold(
      [session({ score: 0.0, label: 'automated-integration' })],
      0,
    );

    expect(counts.bots).toBe(0);
    expect(counts.integrations).toBe(1);
  });

  it('reports a total that adds up', () => {
    const counts = countAtThreshold(
      [
        session({ score: 0.9 }),
        session({ score: 0.1 }),
        session({ score: 0.0, label: 'automated-integration' }),
      ],
      0.7,
    );

    expect(counts.bots + counts.humans + counts.integrations).toBe(counts.total);
  });

  it('handles an empty scan without dividing by zero', () => {
    expect(countAtThreshold([], 0.7)).toEqual({
      bots: 0,
      humans: 0,
      integrations: 0,
      total: 0,
      botRate: 0,
    });
  });
});

describe('failOpenCount', () => {
  it('counts decisions where scoring never ran', () => {
    const count = failOpenCount([
      decision({ heuristic_reason: 'scoring unavailable' }),
      decision({}),
    ]);

    expect(count).toBe(1);
  });

  it('only looks at the recent window, because an old outage is not news', () => {
    const old = new Array(30).fill(0).map(() => decision({ heuristic_reason: 'scoring unavailable' }));

    expect(failOpenCount([decision({}), ...old], 1)).toBe(0);
  });
});

describe('featureInfluence', () => {
  it('sums the absolute weight on each input across the hidden layer', () => {
    // 2 inputs, 2 hidden neurons: [w0, w1, bias] per neuron.
    const weights = [1, -2, 0, 3, 4, 0];

    const influence = featureInfluence(['a', 'b'], weights, 2);

    expect(influence).toEqual([
      { name: 'b', total: 6 },
      { name: 'a', total: 4 },
    ]);
  });

  it('ignores the bias, which belongs to the neuron and not to any input', () => {
    const influence = featureInfluence(['a'], [2, 99], 1);

    expect(influence[0]!.total).toBe(2);
  });

  it('returns nothing rather than guessing when the weights are short', () => {
    expect(featureInfluence(['a', 'b'], [1], 1)).toEqual([
      { name: 'a', total: 1 },
      { name: 'b', total: 0 },
    ]);
  });
});

describe('suspiciouslyPerfect', () => {
  it('flags an evaluation where nothing was missed and nothing misfired', () => {
    expect(suspiciouslyPerfect({ tp: 423, fp: 0, fn: 0, tn: 200 })).toBe(true);
  });

  it('does not flag an ordinary result', () => {
    expect(suspiciouslyPerfect({ tp: 400, fp: 3, fn: 20, tn: 200 })).toBe(false);
  });

  it('does not flag a set that contained only one class', () => {
    // Nothing to separate, so a clean sweep says nothing about the model.
    expect(suspiciouslyPerfect({ tp: 0, fp: 0, fn: 0, tn: 200 })).toBe(false);
  });

  it('does not flag an empty evaluation', () => {
    expect(suspiciouslyPerfect({ tp: 0, fp: 0, fn: 0, tn: 0 })).toBe(false);
  });
});
