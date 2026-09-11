import '@testing-library/jest-dom/vitest';

/**
 * jsdom has no EventSource. Components that open the live stream would throw
 * on construction, so every test would need to know about SSE. This stub is
 * inert: it records nothing and never emits. Tests that care about streaming
 * drive the parsing logic directly instead.
 */
class InertEventSource {
  constructor(readonly url: string) {}
  addEventListener() {}
  removeEventListener() {}
  close() {}
}

if (!('EventSource' in globalThis)) {
  Object.defineProperty(globalThis, 'EventSource', { value: InertEventSource, writable: true });
}
