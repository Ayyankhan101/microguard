import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { FeedbackControl } from './FeedbackControl';

function setup(overrides = {}) {
  const onSubmit = vi.fn().mockResolvedValue({ recorded: true });
  render(
    <FeedbackControl
      decisionId="dec-1"
      currentLabel="bot"
      available
      onSubmit={onSubmit}
      {...overrides}
    />,
  );
  return { onSubmit };
}

describe('FeedbackControl', () => {
  it('offers the opposite label first', () => {
    // The common case is disagreeing, so the disagreement is the primary
    // button and confirmation is the muted one.
    setup();
    expect(screen.getByRole('button', { name: 'human' })).toBeInTheDocument();
  });

  it('sends what the session actually was, not "wrong"', async () => {
    const { onSubmit } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'human' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('dec-1', 'human'));
  });

  it('can confirm a verdict as correct', async () => {
    const { onSubmit } = setup();
    fireEvent.click(screen.getByRole('button', { name: /confirm/ }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('dec-1', 'bot'));
  });

  it('acknowledges a recorded correction', async () => {
    setup();
    fireEvent.click(screen.getByRole('button', { name: 'human' }));
    await waitFor(() => expect(screen.getByText('correction recorded')).toBeInTheDocument());
  });

  it('surfaces a rejected correction instead of silently doing nothing', async () => {
    // An operator who clicked and saw nothing would click again, and the
    // correction would still be lost.
    const onSubmit = vi.fn().mockRejectedValue(new Error('disk full'));
    render(
      <FeedbackControl decisionId="dec-1" currentLabel="bot" available onSubmit={onSubmit} />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'human' }));
    await waitFor(() => expect(screen.getByText('disk full')).toBeInTheDocument());
  });

  it('explains a row that has no id rather than offering a button that fails', () => {
    setup({ decisionId: undefined });
    expect(screen.getByText(/no id/)).toBeInTheDocument();
    expect(screen.queryAllByRole('button')).toHaveLength(0);
  });

  it('explains what is missing when the deployment id is not set', () => {
    setup({ available: false });
    expect(screen.getByText(/--deployment-id/)).toBeInTheDocument();
    expect(screen.queryAllByRole('button')).toHaveLength(0);
  });

  it('disables both buttons while a correction is in flight', async () => {
    const onSubmit = vi.fn(() => new Promise(() => {}));
    render(
      <FeedbackControl decisionId="dec-1" currentLabel="bot" available onSubmit={onSubmit} />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'human' }));
    await waitFor(() =>
      screen.getAllByRole('button').forEach((b) => expect(b).toBeDisabled()),
    );
  });
});
