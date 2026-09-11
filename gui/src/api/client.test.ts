import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, getHealth, runScan } from './client';
import scan from './__fixtures__/scan.json';

function respondWith(body: unknown, init: ResponseInit = {}) {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
      ...init,
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the API client', () => {
  it('parses a response through its schema', async () => {
    vi.stubGlobal('fetch', respondWith({ version: '2.0.0', model_loaded: true, redis_connected: false }));

    await expect(getHealth()).resolves.toEqual({
      version: '2.0.0',
      model_loaded: true,
      redis_connected: false,
    });
  });

  it('rejects a response that does not match the schema', async () => {
    vi.stubGlobal('fetch', respondWith({ version: 2 }));

    await expect(getHealth()).rejects.toThrow();
  });

  it('surfaces the API detail message on an error status', async () => {
    vi.stubGlobal(
      'fetch',
      respondWith({ detail: 'No such sample: nope.log' }, { status: 404 }),
    );

    await expect(runScan({ sample: 'nope.log' })).rejects.toThrow('No such sample: nope.log');
  });

  it('reports the status when the body carries no detail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('nope', { status: 500 })));

    await expect(getHealth()).rejects.toBeInstanceOf(ApiError);
  });

  it('posts the scan request as JSON', async () => {
    const fetchMock = respondWith(scan);
    vi.stubGlobal('fetch', fetchMock);

    await runScan({ sample: 'sample_access.log', threshold: 0.5 });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/scan');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      sample: 'sample_access.log',
      threshold: 0.5,
    });
  });
});
