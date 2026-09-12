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

/**
 * jsdom exposes the Storage constructor but no storage area, and Node 26's own
 * experimental `localStorage` global is undefined without --localstorage-file,
 * so `localStorage` is simply missing under test. The app guards every access,
 * but the tests need somewhere to read and write.
 *
 * The methods go on Storage.prototype rather than the instance so that
 * `vi.spyOn(Storage.prototype, 'setItem')` — how a test simulates private
 * browsing throwing — actually intercepts them.
 */
if (typeof localStorage === 'undefined') {
  const store = new Map<string, string>();

  Object.assign(Storage.prototype, {
    getItem(key: string) {
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key: string, value: string) {
      store.set(key, String(value));
    },
    removeItem(key: string) {
      store.delete(key);
    },
    clear() {
      store.clear();
    },
    key(index: number) {
      return [...store.keys()][index] ?? null;
    },
  });

  Object.defineProperty(globalThis, 'localStorage', {
    value: Object.create(Storage.prototype) as Storage,
    configurable: true,
    writable: true,
  });
}
