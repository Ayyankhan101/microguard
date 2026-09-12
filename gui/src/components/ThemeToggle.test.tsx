import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ThemeToggle } from './ThemeToggle';
import { STORAGE_KEY } from '../theme';

function stubMatchMedia(prefersDark: boolean) {
  const listeners: ((e: { matches: boolean }) => void)[] = [];
  vi.stubGlobal('matchMedia', () => ({
    matches: prefersDark,
    addEventListener: (_t: string, fn: (e: { matches: boolean }) => void) => listeners.push(fn),
    removeEventListener: () => listeners.splice(0, listeners.length),
  }));
  return { emit: (matches: boolean) => listeners.forEach((fn) => fn({ matches })) };
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('ThemeToggle', () => {
  it('starts on system and says so', () => {
    stubMatchMedia(true);

    render(<ThemeToggle />);

    expect(screen.getByRole('button')).toHaveTextContent(/system/i);
  });

  it('applies the resolved theme to the document on mount', () => {
    stubMatchMedia(false);

    render(<ThemeToggle />);

    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('cycles system to light to dark on click', async () => {
    stubMatchMedia(true);
    render(<ThemeToggle />);
    const button = screen.getByRole('button');

    await userEvent.click(button);
    expect(button).toHaveTextContent(/light/i);
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');

    await userEvent.click(button);
    expect(button).toHaveTextContent(/dark/i);
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');

    await userEvent.click(button);
    expect(button).toHaveTextContent(/system/i);
  });

  it('persists the choice', async () => {
    stubMatchMedia(true);
    render(<ThemeToggle />);

    await userEvent.click(screen.getByRole('button'));

    expect(localStorage.getItem(STORAGE_KEY)).toBe('light');
  });

  it('restores a stored choice on mount', () => {
    localStorage.setItem(STORAGE_KEY, 'dark');
    stubMatchMedia(false);

    render(<ThemeToggle />);

    expect(screen.getByRole('button')).toHaveTextContent(/dark/i);
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('follows a live OS change while on system', () => {
    const media = stubMatchMedia(true);
    render(<ThemeToggle />);

    media.emit(false);

    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('ignores a live OS change once a theme was chosen explicitly', async () => {
    const media = stubMatchMedia(true);
    render(<ThemeToggle />);

    await userEvent.click(screen.getByRole('button'));  // -> light
    await userEvent.click(screen.getByRole('button'));  // -> dark
    media.emit(false);

    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('names the next state for a screen reader', () => {
    stubMatchMedia(true);

    render(<ThemeToggle />);

    expect(screen.getByRole('button')).toHaveAccessibleName(/switch to light/i);
  });
});
