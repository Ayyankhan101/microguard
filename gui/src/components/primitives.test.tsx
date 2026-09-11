import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { FeatureBars } from './FeatureBars';
import { Histogram } from './Histogram';
import { LabelBadge, RiskBadge } from './Badges';
import { StatTile } from './StatTile';

describe('RiskBadge', () => {
  it('names the band report.score_label would give the score', () => {
    render(<RiskBadge score={0.91} />);

    expect(screen.getByText('DANGER')).toBeInTheDocument();
  });

  it('colours itself by band', () => {
    render(<RiskBadge score={0.1} />);

    expect(screen.getByText('SAFE')).toHaveClass('badge--safe');
  });
});

describe('LabelBadge', () => {
  it('shows the verdict', () => {
    render(<LabelBadge label="bot" />);

    expect(screen.getByText('bot')).toHaveClass('badge--danger');
  });

  it('marks an automated integration as allowed rather than dangerous', () => {
    render(<LabelBadge label="automated-integration" />);

    expect(screen.getByText('automated-integration')).toHaveClass('badge--purple');
  });
});

describe('StatTile', () => {
  it('shows a value and its label', () => {
    render(<StatTile value="12" label="blocked" />);

    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('blocked')).toBeInTheDocument();
  });

  it('shows a note when given one', () => {
    render(<StatTile value="12" label="blocked" note="since start" />);

    expect(screen.getByText('since start')).toBeInTheDocument();
  });
});

describe('Histogram', () => {
  it('draws one bar per bucket', () => {
    render(<Histogram buckets={[1, 2, 3]} />);

    expect(screen.getAllByTestId('bar')).toHaveLength(3);
  });

  it('scales bars against the tallest bucket', () => {
    render(<Histogram buckets={[5, 10]} />);

    const bars = screen.getAllByTestId('bar');
    expect(bars[0]).toHaveStyle({ height: '50%' });
    expect(bars[1]).toHaveStyle({ height: '100%' });
  });

  it('does not divide by zero when nothing has been recorded', () => {
    render(<Histogram buckets={[0, 0]} />);

    expect(screen.getAllByTestId('bar')[0]).toHaveStyle({ height: '0%' });
  });

  it('labels each bar with its score range and count', () => {
    render(<Histogram buckets={new Array(20).fill(0).map((_, i) => i)} />);

    expect(screen.getAllByTestId('bar')[19]).toHaveAttribute('title', '0.95-1.00: 19');
  });
});

describe('FeatureBars', () => {
  it('lists every feature it is given', () => {
    render(<FeatureBars features={{ error_rate: 0.5, night_ratio: 0.25 }} />);

    expect(screen.getByText('error_rate')).toBeInTheDocument();
    expect(screen.getByText('night_ratio')).toBeInTheDocument();
  });

  it('shows the raw value, because features are not all 0-1', () => {
    render(<FeatureBars features={{ requests_per_minute_1m: 1200 }} />);

    expect(screen.getByText('1200')).toBeInTheDocument();
  });

  it('orders by magnitude relative to the other features, largest first', () => {
    render(<FeatureBars features={{ a: 1, b: 50, c: 10 }} />);

    const names = screen.getAllByTestId('feature-name').map((node) => node.textContent);
    expect(names).toEqual(['b', 'c', 'a']);
  });
});
