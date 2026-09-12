/**
 * The theme preference is three-state (system/light/dark) but the DOM only
 * ever sees two. These tests pin that resolution, because it is the part that
 * breaks silently: a wrong resolve shows the wrong colours with no error.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  STORAGE_KEY,
  applyTheme,
  nextPreference,
  readPreference,
  resolveTheme,
  storePreference,
  systemTheme,
  watchSystemTheme,
} from './theme';

/** jsdom has no matchMedia. This stands in, and lets a test drive the OS. */
function stubMatchMedia(prefersDark: boolean) {
  const listeners: ((e: { matches: boolean }) => void)[] = [];
  const mql = {
    matches: prefersDark,
    addEventListener: (_type: string, fn: (e: { matches: boolean }) => void) =>
      listeners.push(fn),
    removeEventListener: (_type: string, fn: (e: { matches: boolean }) => void) => {
      const i = listeners.indexOf(fn);
      if (i >= 0) listeners.splice(i, 1);
    },
  };
  vi.stubGlobal('matchMedia', () => mql);
  return {
    listenerCount: () => listeners.length,
    emit: (matches: boolean) => listeners.forEach((fn) => fn({ matches })),
  };
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});

afterEach(() => {
  vi.unstubAllGlobals();
  // spyOn is not auto-restored, and the storage-failure tests would otherwise
  // leave getItem/setItem throwing for everything that follows.
  vi.restoreAllMocks();
});

describe('readPreference', () => {
  it('defaults to following the system', () => {
    expect(readPreference()).toBe('system');
  });

  it('returns a stored choice', () => {
    localStorage.setItem(STORAGE_KEY, 'light');

    expect(readPreference()).toBe('light');
  });

  it('ignores a stored value that is not a preference', () => {
    localStorage.setItem(STORAGE_KEY, 'chartreuse');

    expect(readPreference()).toBe('system');
  });

  it('survives localStorage being unavailable', () => {
    // Private browsing and some embedded webviews throw on access.
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });

    expect(readPreference()).toBe('system');
  });
});

describe('storePreference', () => {
  it('persists the choice', () => {
    storePreference('dark');

    expect(localStorage.getItem(STORAGE_KEY)).toBe('dark');
  });

  it('does not throw when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });

    expect(() => storePreference('dark')).not.toThrow();
  });
});

describe('systemTheme', () => {
  it('reads dark from the OS', () => {
    stubMatchMedia(true);

    expect(systemTheme()).toBe('dark');
  });

  it('reads light from the OS', () => {
    stubMatchMedia(false);

    expect(systemTheme()).toBe('light');
  });

  it('falls back to dark where matchMedia does not exist', () => {
    vi.stubGlobal('matchMedia', undefined);

    expect(systemTheme()).toBe('dark');
  });
});

describe('resolveTheme', () => {
  it('follows the OS when the preference is system', () => {
    stubMatchMedia(false);

    expect(resolveTheme('system')).toBe('light');
  });

  it('ignores the OS when a theme was chosen explicitly', () => {
    stubMatchMedia(false);

    expect(resolveTheme('dark')).toBe('dark');
  });
});

describe('applyTheme', () => {
  it('writes the resolved theme onto the document', () => {
    applyTheme('light');

    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('replaces a previous value rather than appending', () => {
    applyTheme('light');
    applyTheme('dark');

    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });
});

describe('nextPreference', () => {
  it('cycles system to light to dark and back', () => {
    expect(nextPreference('system')).toBe('light');
    expect(nextPreference('light')).toBe('dark');
    expect(nextPreference('dark')).toBe('system');
  });
});

describe('watchSystemTheme', () => {
  it('reports an OS change', () => {
    const media = stubMatchMedia(true);
    const seen: string[] = [];

    watchSystemTheme((theme) => seen.push(theme));
    media.emit(false);

    expect(seen).toEqual(['light']);
  });

  it('stops reporting once unsubscribed', () => {
    const media = stubMatchMedia(true);
    const seen: string[] = [];

    const stop = watchSystemTheme((theme) => seen.push(theme));
    stop();
    media.emit(false);

    expect(seen).toEqual([]);
    expect(media.listenerCount()).toBe(0);
  });

  it('is inert where matchMedia does not exist', () => {
    vi.stubGlobal('matchMedia', undefined);

    expect(() => watchSystemTheme(() => {})()).not.toThrow();
  });
});
