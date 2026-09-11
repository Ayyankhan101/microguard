/**
 * The score ruler is the one instrument this UI is built around: it shows
 * what the rules said, what the model said, where the blend landed, and where
 * the bar was — the exact read order docs/howto-tune-blocking.md prescribes.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ScoreRuler } from './ScoreRuler';

describe('ScoreRuler', () => {
  it('plots the blended score at its position along the scale', () => {
    render(<ScoreRuler blended={0.25} />);

    expect(screen.getByTestId('mark-blended')).toHaveStyle({ left: '25%' });
  });

  it('plots the model and heuristic scores separately', () => {
    render(<ScoreRuler blended={0.5} modelScore={0.9} heuristicConfidence={0.1} />);

    expect(screen.getByTestId('mark-model')).toHaveStyle({ left: '90%' });
    expect(screen.getByTestId('mark-heuristic')).toHaveStyle({ left: '10%' });
  });

  it('omits marks it was not given, rather than drawing them at zero', () => {
    render(<ScoreRuler blended={0.5} />);

    expect(screen.queryByTestId('mark-model')).not.toBeInTheDocument();
    expect(screen.queryByTestId('mark-heuristic')).not.toBeInTheDocument();
  });

  it('draws the threshold as a gate labelled with its value', () => {
    render(<ScoreRuler blended={0.2} threshold={0.85} />);

    const gate = screen.getByTestId('gate');
    expect(gate).toHaveStyle({ left: '85%' });
    expect(gate).toHaveAttribute('data-label', '0.85');
  });

  it('prints the gate to two decimals, like every other score on screen', () => {
    render(<ScoreRuler blended={0.2} threshold={0.7} />);

    expect(screen.getByTestId('gate')).toHaveAttribute('data-label', '0.70');
  });

  it('draws no gate when no threshold applied', () => {
    render(<ScoreRuler blended={0.2} threshold={null} />);

    expect(screen.queryByTestId('gate')).not.toBeInTheDocument();
  });

  it('clamps a score outside the scale instead of overflowing the track', () => {
    render(<ScoreRuler blended={1.4} />);

    expect(screen.getByTestId('mark-blended')).toHaveStyle({ left: '100%' });
  });

  it('describes the decision for a screen reader', () => {
    render(<ScoreRuler blended={0.91} threshold={0.85} />);

    expect(screen.getByRole('img')).toHaveAccessibleName(
      'Blended score 0.91, above the 0.85 block threshold',
    );
  });

  it('says when the score is below the threshold', () => {
    render(<ScoreRuler blended={0.1} threshold={0.85} />);

    expect(screen.getByRole('img')).toHaveAccessibleName(
      'Blended score 0.10, below the 0.85 block threshold',
    );
  });
});

describe('ScoreRuler in compact form', () => {
  it('still marks where the gate is', () => {
    render(<ScoreRuler compact blended={0.9} threshold={0.85} />);

    expect(screen.getByTestId('gate')).toBeInTheDocument();
  });

  it('drops the printed threshold, which repeats down a list of rows', () => {
    render(<ScoreRuler compact blended={0.9} threshold={0.85} />);

    expect(screen.getByTestId('gate')).not.toHaveAttribute('data-label');
  });

  it('keeps the threshold in the label a screen reader gets', () => {
    render(<ScoreRuler compact blended={0.9} threshold={0.85} />);

    expect(screen.getByRole('img')).toHaveAccessibleName(
      'Blended score 0.90, above the 0.85 block threshold',
    );
  });
});
