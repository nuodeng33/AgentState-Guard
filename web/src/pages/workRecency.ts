const KEY = 'asg.lastOpenedWork';

export function lastOpenedWork(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function recordOpenedWork(id: string, visible: readonly { id: string }[]): void {
  if (!visible.some(item => item.id === id)) return;
  try {
    window.localStorage.setItem(KEY, id);
  } catch {
    // Browsers can block local storage. The work index remains available.
  }
}
