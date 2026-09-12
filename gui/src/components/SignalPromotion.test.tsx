import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { SignalPromotion } from './SignalPromotion';

const KNOWN = ['abuseipdb', 'fingerprint', 'hosting', 'tor'];

function setup(overrides = {}) {
  const onToggle = vi.fn();
  render(
    <SignalPromotion
      known={KNOWN}
      promoted={['tor']}
      writable
      saving={false}
      error={null}
      onToggle={onToggle}
      {...overrides}
    />,
  );
  return { onToggle };
}

describe('SignalPromotion', () => {
  it('marks promoted and unpromoted sources differently', () => {
    setup();
    expect(screen.getAllByText('enforced')).toHaveLength(1);
    expect(screen.getAllByText('observe-only')).toHaveLength(KNOWN.length - 1);
  });

  it('sends the full list when promoting, not a delta', () => {
    // The endpoint treats promoted_signals as absolute, so a partial list
    // would silently un-promote everything it omitted.
    const { onToggle } = setup();
    fireEvent.click(screen.getByRole('checkbox', { name: /fingerprint/i }));
    expect(onToggle).toHaveBeenCalledWith(['tor', 'fingerprint']);
  });

  it('sends the remaining list when demoting', () => {
    const { onToggle } = setup({ promoted: ['tor', 'fingerprint'] });
    fireEvent.click(screen.getByRole('checkbox', { name: /tor/i }));
    expect(onToggle).toHaveBeenCalledWith(['fingerprint']);
  });

  it('disables every control when writes are off', () => {
    // Read-only is the default posture. The UI must not offer a control that
    // would 403, or the operator learns the rule from an error message.
    setup({ writable: false });
    screen.getAllByRole('checkbox').forEach((box) => expect(box).toBeDisabled());
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
  });

  it('explains what unpromoted means when writable', () => {
    setup();
    expect(screen.getByText(/recorded but never block/)).toBeInTheDocument();
  });

  it('disables controls while a save is in flight', () => {
    setup({ saving: true });
    screen.getAllByRole('checkbox').forEach((box) => expect(box).toBeDisabled());
    expect(screen.getByText('saving…')).toBeInTheDocument();
  });

  it('surfaces a rejected promotion', () => {
    setup({ error: 'unknown signal source(s): [torr]' });
    expect(screen.getByText(/unknown signal source/)).toBeInTheDocument();
  });
});
