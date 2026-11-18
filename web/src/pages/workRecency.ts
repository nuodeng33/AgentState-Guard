const KEY = 'asg.lastOpenedWork';
const CARD_KEY = 'asg.recentWorkCards';
const MAX_ENTRIES = 32;
const MAX_ID_LENGTH = 128;
type CardEntry = [workId: string, cardKey: string];

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

function readCardEntries(): CardEntry[] {
  try {
    const raw = window.localStorage.getItem(CARD_KEY);
    if (!raw || raw.length > 16_384) return [];
    const entries: unknown = JSON.parse(raw);
    if (!Array.isArray(entries)) return [];
    return entries.slice(0, MAX_ENTRIES).filter((entry): entry is CardEntry =>
      Array.isArray(entry) && entry.length === 2 &&
      entry.every(value => typeof value === 'string' && value.length > 0 && value.length <= MAX_ID_LENGTH));
  } catch {
    return [];
  }
}

function saveCardEntries(entries: CardEntry[]): void {
  try {
    window.localStorage.setItem(CARD_KEY, JSON.stringify(entries.slice(0, MAX_ENTRIES)));
  } catch {
    // This preference is optional when local storage is unavailable.
  }
}

export function lastSelectedCard(workId: string): string | undefined {
  return readCardEntries().find(([id]) => id === workId)?.[1];
}

export function recordSelectedCard(workId: string, cardKey: string): void {
  if (!workId || !cardKey || workId.length > MAX_ID_LENGTH || cardKey.length > MAX_ID_LENGTH) return;
  saveCardEntries([[workId, cardKey], ...readCardEntries().filter(([id]) => id !== workId)]);
}

export function forgetSelectedCard(workId: string, cardKey: string): void {
  const entries = readCardEntries();
  const kept = entries.filter(([id, key]) => id !== workId || key !== cardKey);
  if (kept.length !== entries.length) saveCardEntries(kept);
}
