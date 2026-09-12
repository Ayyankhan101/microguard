import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { SignalStatus, formatAge } from './SignalStatus';

describe('SignalStatus', () => {
  it('names why the refresher is absent, not just that it is', () => {
    // "no redis", "redis unreachable" and "never ran" need three different
    // fixes, so collapsing them to "not running" would cost the operator the
    // one piece of information that matters.
    render(<SignalStatus health={{ running: false, reason: 'never ran', sources: [] }} />);
    expect(screen.getByText(/never ran/)).toBeInTheDocument();
  });

  it('falls back when no reason was reported', () => {
    render(<SignalStatus health={{ running: false, sources: [] }} />);
    expect(screen.getByText(/not running/)).toBeInTheDocument();
  });

  it('reports a failing source even while the refresher is alive', () => {
    // The dangerous case: the process is up, so nothing looks wrong, but one
    // feed has been returning nothing for a day.
    render(
      <SignalStatus
        health={{
          running: true,
          age_seconds: 10,
          sources: [
            { name: 'tor', ok: true, entries: 1200 },
            { name: 'hosting', ok: false, entries: 0, error: 'network down' },
          ],
        }}
      />,
    );
    expect(screen.getByText(/1 source failing/)).toBeInTheDocument();
    expect(screen.getByTitle(/hosting: network down/)).toBeInTheDocument();
  });

  it('pluralizes multiple failures', () => {
    render(
      <SignalStatus
        health={{
          running: true,
          sources: [
            { name: 'tor', ok: false, entries: 0 },
            { name: 'hosting', ok: false, entries: 0 },
          ],
        }}
      />,
    );
    expect(screen.getByText(/2 sources failing/)).toBeInTheDocument();
  });

  it('shows the age of the last pass when everything is healthy', () => {
    render(
      <SignalStatus
        health={{
          running: true,
          age_seconds: 45,
          sources: [{ name: 'tor', ok: true, entries: 1200 }],
        }}
      />,
    );
    expect(screen.getByText(/signals ok \(45s ago\)/)).toBeInTheDocument();
    expect(screen.getByTitle('tor: 1200 entries')).toBeInTheDocument();
  });
});

describe('formatAge', () => {
  it('uses seconds below ninety', () => {
    expect(formatAge(0)).toBe('0s ago');
    expect(formatAge(89)).toBe('89s ago');
  });

  it('switches to minutes, then hours', () => {
    expect(formatAge(90)).toBe('2m ago');
    expect(formatAge(5399)).toBe('90m ago');
    expect(formatAge(5400)).toBe('2h ago');
  });
});
