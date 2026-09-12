import { useCallback, useEffect, useState } from 'react';

import {
  applyTheme,
  nextPreference,
  readPreference,
  resolveTheme,
  storePreference,
  watchSystemTheme,
  type ThemePreference,
} from '../theme';

const LABEL: Record<ThemePreference, string> = {
  system: 'system',
  light: 'light',
  dark: 'dark',
};

const ICON: Record<ThemePreference, string> = {
  system: '◐',
  light: '☀',
  dark: '☾',
};

/**
 * Cycles system → light → dark.
 *
 * Three states rather than two because "follow the OS" is a real preference,
 * not the absence of one: without it, choosing light once would pin the
 * dashboard to light even after the machine switches to dark for the evening.
 */
export function ThemeToggle() {
  const [preference, setPreference] = useState<ThemePreference>(readPreference);

  // Resolve on mount and whenever the preference changes.
  useEffect(() => {
    applyTheme(resolveTheme(preference));
  }, [preference]);

  // Only meaningful while following the OS; an explicit choice outranks it.
  useEffect(() => {
    if (preference !== 'system') return;
    return watchSystemTheme(applyTheme);
  }, [preference]);

  const advance = useCallback(() => {
    setPreference((current) => {
      const next = nextPreference(current);
      storePreference(next);
      return next;
    });
  }, []);

  const upcoming = nextPreference(preference);

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={advance}
      aria-label={`Theme: ${LABEL[preference]}. Switch to ${LABEL[upcoming]}.`}
      title={`Theme: ${LABEL[preference]}`}
    >
      <span aria-hidden="true">{ICON[preference]}</span>
      {LABEL[preference]}
    </button>
  );
}
