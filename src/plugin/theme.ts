// Light or dark. The page is light unless the visitor switched dark mode on in Settings,
// in which case this browser remembers it. The stylesheet does the rest: every colour
// that differs is a variable under :root[data-theme='dark'] in plugin.css.
//
// index.html reads the same key in a one-line script in <head>, so a dark page never
// flashes light while this module loads.

export type Theme = 'light' | 'dark';

const KEY = 'plugin-playground-theme';

export function getTheme(): Theme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

export function setTheme(theme: Theme): void {
  if (theme === 'dark') document.documentElement.dataset.theme = 'dark';
  else delete document.documentElement.dataset.theme;
  try {
    if (theme === 'dark') localStorage.setItem(KEY, 'dark');
    else localStorage.removeItem(KEY);
  } catch {
    // Storage can be blocked (private windows); the switch still works for this visit.
  }
}
