/**
 * Theme preference, resolved to something the DOM can act on.
 *
 * The preference is three-state and the DOM is two-state. Rather than express
 * "follow the system" in CSS with a prefers-color-scheme block — which then has
 * to be kept from fighting an explicit override — this resolves the preference
 * in one place and always writes a concrete `data-theme` onto <html>. CSS then
 * needs two token blocks and no media query, and there is exactly one place
 * where the resolution can be wrong.
 */

export type ThemePreference = 'system' | 'light' | 'dark';
export type ResolvedTheme = 'light' | 'dark';

export const STORAGE_KEY = 'microguard-theme';

const PREFERENCES: ThemePreference[] = ['system', 'light', 'dark'];

const DARK_QUERY = '(prefers-color-scheme: dark)';

function isPreference(value: unknown): value is ThemePreference {
  return typeof value === 'string' && (PREFERENCES as string[]).includes(value);
}

/** The stored choice, or `system` when there is none or it is unreadable. */
export function readPreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return isPreference(stored) ? stored : 'system';
  } catch {
    // Private browsing and some embedded webviews throw on access. A theme is
    // not worth failing over.
    return 'system';
  }
}

export function storePreference(preference: ThemePreference): void {
  try {
    localStorage.setItem(STORAGE_KEY, preference);
  } catch {
    // Same reasoning: the choice just will not survive a reload.
  }
}

/** What the OS is asking for. Dark when it has no opinion, matching the
 *  dashboard's original and only look. */
export function systemTheme(): ResolvedTheme {
  if (typeof matchMedia !== 'function') return 'dark';
  return matchMedia(DARK_QUERY).matches ? 'dark' : 'light';
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  return preference === 'system' ? systemTheme() : preference;
}

export function applyTheme(resolved: ResolvedTheme): void {
  document.documentElement.setAttribute('data-theme', resolved);
}

export function nextPreference(current: ThemePreference): ThemePreference {
  const index = PREFERENCES.indexOf(current);
  return PREFERENCES[(index + 1) % PREFERENCES.length] ?? 'system';
}

/**
 * Call `onChange` when the OS theme changes. Returns an unsubscribe.
 *
 * Only meaningful while the preference is `system`; the caller decides whether
 * to act on it.
 */
export function watchSystemTheme(
  onChange: (theme: ResolvedTheme) => void,
): () => void {
  if (typeof matchMedia !== 'function') return () => {};

  const query = matchMedia(DARK_QUERY);
  const handler = (event: MediaQueryListEvent) =>
    onChange(event.matches ? 'dark' : 'light');

  query.addEventListener('change', handler);
  return () => query.removeEventListener('change', handler);
}
