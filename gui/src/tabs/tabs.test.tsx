/**
 * Tab-level render checks: the states a person actually lands in — nothing
 * loaded yet, nothing to show, and something went wrong. The arithmetic these
 * tabs do is covered in logic.test.ts.
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LiveTab } from './LiveTab';
import { ModelTab } from './ModelTab';
import { ScanTab } from './ScanTab';
import { renderWithClient } from '../test-utils';
import model from '../api/__fixtures__/model.json';
import modelEvaluation from '../api/__fixtures__/model-evaluation.json';
import scan from '../api/__fixtures__/scan.json';
import liveEvents from '../api/__fixtures__/live-events.json';
import liveStats from '../api/__fixtures__/live-stats.json';

type Route = unknown | { body: unknown; status: number };

function stub(routes: Record<string, Route>) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string) => {
      // Longest key first, so '/api/scan/samples' wins over '/api/scan'.
      const match = Object.keys(routes)
        .sort((a, b) => b.length - a.length)
        .find((key) => url.includes(key));
      const route = match ? (routes[match] as { body?: unknown; status?: number }) : undefined;
      const hasStatus = route && typeof route === 'object' && 'status' in route;
      return Promise.resolve(
        new Response(JSON.stringify(hasStatus ? route.body : (route ?? {})), {
          status: hasStatus ? route.status : match ? 200 : 404,
          headers: { 'content-type': 'application/json' },
        }),
      );
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ScanTab', () => {
  it('invites a scan before anything has been run', async () => {
    stub({ '/api/scan/samples': { samples: [] } });

    renderWithClient(<ScanTab />);

    expect(await screen.findByText(/drop an access log here/i)).toBeInTheDocument();
  });

  it('offers each bundled sample with its size', async () => {
    stub({
      '/api/scan/samples': { samples: [{ name: 'sample_access.log', bytes: 100, lines: 20 }] },
    });

    renderWithClient(<ScanTab />);

    expect(await screen.findByRole('option', { name: /sample_access\.log \(20 lines\)/ })).toBeInTheDocument();
  });

  it('shows the sessions once a scan returns', async () => {
    stub({ '/api/scan/samples': { samples: [{ name: 'sample_access.log', bytes: 1, lines: 20 }] }, '/api/scan': scan });

    renderWithClient(<ScanTab />);
    await screen.findByRole('option', { name: /sample_access\.log/ });
    await userEvent.selectOptions(screen.getByLabelText('bundled sample'), 'sample_access.log');

    expect(await screen.findByText('bot sessions')).toBeInTheDocument();
  });

  it('says what went wrong when the scan fails', async () => {
    stub({
      '/api/scan/samples': { samples: [{ name: 'x.log', bytes: 1, lines: 1 }] },
      '/api/scan': { body: { detail: 'No such sample: x.log' }, status: 404 },
    });

    renderWithClient(<ScanTab />);
    await screen.findByRole('option', { name: /x\.log/ });
    await userEvent.selectOptions(screen.getByLabelText('bundled sample'), 'x.log');

    expect(await screen.findByText('No such sample: x.log')).toBeInTheDocument();
  });
});

describe('LiveTab', () => {
  it('explains how to get traffic when nothing has been scored', async () => {
    stub({
      '/api/live/events': { events: [] },
      '/api/live/stats': liveStats,
      '/api/live/config': { block_threshold: null, writable: false },
    });

    renderWithClient(<LiveTab />);

    expect(await screen.findByText(/nothing scored yet/i)).toBeInTheDocument();
  });

  it('lists the decisions the live path made', async () => {
    stub({
      '/api/live/events': liveEvents,
      '/api/live/stats': liveStats,
      '/api/live/config': { block_threshold: null, writable: false },
    });

    renderWithClient(<LiveTab />);

    expect(await screen.findByText('203.0.113.9')).toBeInTheDocument();
  });

  it('says the threshold is read-only unless writes were enabled', async () => {
    stub({
      '/api/live/events': { events: [] },
      '/api/live/stats': liveStats,
      '/api/live/config': { block_threshold: 0.85, writable: false },
    });

    renderWithClient(<LiveTab />);

    expect(await screen.findByText(/--allow-config-writes/)).toBeInTheDocument();
  });

  it('offers the threshold slider when writes are enabled', async () => {
    stub({
      '/api/live/events': { events: [] },
      '/api/live/stats': liveStats,
      '/api/live/config': { block_threshold: 0.85, writable: true },
    });

    renderWithClient(<LiveTab />);

    expect(await screen.findByLabelText('block threshold')).toBeInTheDocument();
  });
});

describe('ModelTab', () => {
  it('shows the architecture and parameter count', async () => {
    stub({ '/api/model/evaluate': modelEvaluation, '/api/model': model });

    renderWithClient(<ModelTab />);

    expect(await screen.findByText('19 → 4 → 1')).toBeInTheDocument();
    expect(await screen.findByText('85')).toBeInTheDocument();
  });

  it('reports metrics for the selected evaluation set', async () => {
    stub({ '/api/model/evaluate': modelEvaluation, '/api/model': model });

    renderWithClient(<ModelTab />);

    expect(await screen.findByText('precision')).toBeInTheDocument();
  });

  it('says so when there is no trained model to describe', async () => {
    stub({ '/api/model': { body: { detail: 'No trained model available' }, status: 503 } });

    renderWithClient(<ModelTab />);

    await waitFor(() =>
      expect(screen.getByText('No trained model available')).toBeInTheDocument(),
    );
  });
});
