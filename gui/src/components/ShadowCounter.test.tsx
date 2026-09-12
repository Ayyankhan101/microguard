import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { ShadowCounter } from './ShadowCounter';
import { thresholdImpact, wouldBlock } from '../tabs/logic';

const DECISIONS = [
  { score: 0.95, label: 'bot' },
  { score: 0.9, label: 'bot' },
  { score: 0.6, label: 'human' },
  { score: 0.2, label: 'human' },
];

describe('wouldBlock', () => {
  it('uses a strict comparison, matching the scorer', () => {
    // live/scorer.py: a score sitting exactly on the bar has not cleared it.
    // That is what makes 1.00 a real never-block setting.
    expect(wouldBlock([0.85], 0.85)).toBe(0);
    expect(wouldBlock([0.851], 0.85)).toBe(1);
  });

  it('blocks nothing at 1.00 even for a saturated score', () => {
    expect(wouldBlock([1.0, 1.0], 1.0)).toBe(0);
  });
});

describe('thresholdImpact', () => {
  it('reports the delta against what actually happened', () => {
    const impact = thresholdImpact(DECISIONS, 0.5);
    expect(impact.actual).toBe(2);
    expect(impact.projected).toBe(3);
    expect(impact.delta).toBe(1);
    expect(impact.sampled).toBe(4);
  });

  it('reports a negative delta when loosening', () => {
    expect(thresholdImpact(DECISIONS, 0.99).delta).toBe(-2);
  });
});

describe('ShadowCounter', () => {
  it('says how many more would be blocked', () => {
    render(<ShadowCounter decisions={DECISIONS} threshold={0.5} />);
    expect(screen.getByText('1 more')).toBeInTheDocument();
  });

  it('says how many fewer when loosening', () => {
    render(<ShadowCounter decisions={DECISIONS} threshold={0.99} />);
    expect(screen.getByText('2 fewer')).toBeInTheDocument();
  });

  it('says when nothing would change', () => {
    render(<ShadowCounter decisions={DECISIONS} threshold={0.85} />);
    expect(screen.getByText(/same as the 2 actually blocked/)).toBeInTheDocument();
  });

  it('carries its sample size, because the ring is capped', () => {
    render(<ShadowCounter decisions={DECISIONS} threshold={0.5} />);
    expect(screen.getByText(/of the last 4/)).toBeInTheDocument();
  });

  it('asks for traffic rather than showing a meaningless zero', () => {
    render(<ShadowCounter decisions={[]} threshold={0.85} />);
    expect(screen.getByText(/needs traffic/)).toBeInTheDocument();
  });

  it('says nothing useful is possible without a threshold', () => {
    render(<ShadowCounter decisions={DECISIONS} threshold={null} />);
    expect(screen.getByText(/needs traffic/)).toBeInTheDocument();
  });
});
