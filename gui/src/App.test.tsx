import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';
import { renderWithClient } from './test-utils';

/**
 * The rail renders health and the live tab opens the feed, so every App test
 * needs the network stubbed. These tests are about tab routing only.
 */
function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string) => {
      const body: Record<string, unknown> = url.includes('/health')
        ? { version: '2.0.0', model_loaded: true, redis_connected: true }
        : url.includes('/live/events')
          ? { events: [] }
          : url.includes('/live/stats')
            ? {
                total: 0,
                blocked: 0,
                allowed: 0,
                bot_rate: 0,
                avg_score: 0,
                histogram: new Array(20).fill(0),
                top_blocked_ips: [],
                started_at: null,
              }
            : url.includes('/live/config')
              ? { block_threshold: null, writable: false }
              : {};
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      );
    }),
  );
}

function renderApp() {
  stubApi();
  window.location.hash = '';
  return renderWithClient(<App />);
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
});

describe('App', () => {
  it('opens on the live tab', async () => {
    renderApp();

    expect(await screen.findByRole('button', { name: /live/i })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('switches tabs and records it in the URL so a view can be linked', async () => {
    renderApp();

    await userEvent.click(await screen.findByRole('button', { name: /model/i }));

    expect(window.location.hash).toBe('#model');
  });

  it('opens on the tab named in the URL', async () => {
    window.location.hash = '#scan';
    stubApi();

    renderWithClient(<App />);

    expect(await screen.findByRole('button', { name: /scan/i })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('falls back to live for an unknown hash rather than rendering nothing', async () => {
    window.location.hash = '#nonsense';
    stubApi();

    renderWithClient(<App />);

    expect(await screen.findByRole('button', { name: /live/i })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });
});
