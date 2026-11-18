type CardBounds = { x: number; y: number; width: number; height: number };
type Viewport = { x: number; y: number; zoom: number };

const margin = 24;
const minimumReadingZoom = .85;

export function frameTwoCards(cards: readonly CardBounds[], width: number, height: number, preferredZoom: number): Viewport | null {
  if (cards.length !== 2 || ![width, height, preferredZoom].every(Number.isFinite)) return null;
  if (cards.some(card => ![card.x, card.y, card.width, card.height].every(Number.isFinite) || card.width <= 0 || card.height <= 0)) return null;
  const [a, b] = cards;
  const aRight = a.x + a.width;
  const bRight = b.x + b.width;
  const aBottom = a.y + a.height;
  const bBottom = b.y + b.height;
  if (a.x < bRight && b.x < aRight && a.y < bBottom && b.y < aBottom) return null;

  const left = Math.min(a.x, b.x);
  const top = Math.min(a.y, b.y);
  const spanX = Math.max(aRight, bRight) - left;
  const spanY = Math.max(aBottom, bBottom) - top;
  const scale = Math.min((width - margin * 2) / spanX, (height - margin * 2) / spanY, 2);
  if (scale < minimumReadingZoom) return null;
  const zoom = Math.min(scale, Math.max(preferredZoom, minimumReadingZoom));
  const x = (width - spanX * zoom) / 2 - left * zoom;
  const y = margin - top * zoom;
  // Persisted view offsets have a server-side limit. Clamping could clip a card.
  if (Math.abs(x) > 100000 || Math.abs(y) > 100000) return null;
  return { x, y, zoom };
}
