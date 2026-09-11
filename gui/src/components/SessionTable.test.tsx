import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { SessionTable } from './SessionTable';
import type { ScanSession } from '../api/schemas';

function session(overrides: Partial<ScanSession> = {}): ScanSession {
  return {
    ip: '10.0.0.1',
    score: 0.2,
    model_score: 0.1,
    heuristic_label: 'human',
    heuristic_confidence: 0.5,
    heuristic_reason: 'no strong signals either way',
    label: 'human',
    request_count: 3,
    duration: 12,
    top_endpoint: '/api/items',
    user_agent: 'Mozilla/5.0',
    features: {},
    ...overrides,
  };
}

function rowIps() {
  return within(screen.getByRole('table'))
    .getAllByTestId('row-ip')
    .map((node) => node.textContent);
}

describe('SessionTable', () => {
  it('sorts by score, highest first, so the worst is on top', () => {
    render(
      <SessionTable
        sessions={[session({ ip: 'low', score: 0.1 }), session({ ip: 'high', score: 0.9 })]}
      />,
    );

    expect(rowIps()).toEqual(['high', 'low']);
  });

  it('sorts by a column when its header is clicked', async () => {
    render(
      <SessionTable
        sessions={[
          session({ ip: 'few', request_count: 1 }),
          session({ ip: 'many', request_count: 99 }),
        ]}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /requests/i }));

    expect(rowIps()).toEqual(['many', 'few']);
  });

  it('reverses the sort on a second click', async () => {
    render(
      <SessionTable
        sessions={[
          session({ ip: 'few', request_count: 1 }),
          session({ ip: 'many', request_count: 99 }),
        ]}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /requests/i }));
    await userEvent.click(screen.getByRole('button', { name: /requests/i }));

    expect(rowIps()).toEqual(['few', 'many']);
  });

  it('filters on IP, endpoint, user agent and reason together', async () => {
    render(
      <SessionTable
        sessions={[
          session({ ip: '10.0.0.1', user_agent: 'curl/8.0' }),
          session({ ip: '10.0.0.2', user_agent: 'Mozilla/5.0' }),
        ]}
        filter="curl"
      />,
    );

    expect(rowIps()).toEqual(['10.0.0.1']);
  });

  it('says so when a filter matches nothing', () => {
    render(<SessionTable sessions={[session()]} filter="zzz" />);

    expect(screen.getByText(/no sessions match/i)).toBeInTheDocument();
  });

  it('labels a row against the threshold in play, not the one the scan used', () => {
    render(<SessionTable sessions={[session({ score: 0.5, label: 'human' })]} threshold={0.4} />);

    expect(screen.getByText('bot')).toBeInTheDocument();
  });

  it('leaves automated integrations allowed however low the threshold goes', () => {
    render(
      <SessionTable
        sessions={[session({ score: 0.0, label: 'automated-integration' })]}
        threshold={0}
      />,
    );

    expect(screen.getByText('automated-integration')).toBeInTheDocument();
  });

  it('reports the selected session to its parent', async () => {
    const onSelect = vi.fn();
    render(<SessionTable sessions={[session({ ip: '10.0.0.9' })]} onSelect={onSelect} />);

    await userEvent.click(screen.getByRole('row', { name: /10\.0\.0\.9/ }));

    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ ip: '10.0.0.9' }));
  });

  it('shows nothing but an invitation when there are no sessions at all', () => {
    render(<SessionTable sessions={[]} />);

    expect(screen.getByText(/no sessions/i)).toBeInTheDocument();
  });
});
