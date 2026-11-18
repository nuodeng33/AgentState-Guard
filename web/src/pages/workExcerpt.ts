/** Keep document previews small, even for a long manuscript or a single unbroken line. */
export function workExcerpt(markdown: string, title?: string): string[] {
  const paragraphs: string[] = [];
  let heading = '';
  let fence = '';
  let fallback = '';
  const shorten = (line: string) => line.length > 220 ? line.slice(0, 220).trimEnd() + '…' : line;
  for (const raw of markdown.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const delimiter = /^(`{3,}|~{3,})/.exec(line)?.[0];
    if (delimiter && !fence) { fence = delimiter; continue; }
    if (fence && delimiter?.startsWith(fence)) { fence = ''; continue; }
    if (fence || line.startsWith('|')) { fallback ||= shorten(line); continue; }
    const section = /^#{1,6}\s+(.+)$/.exec(line);
    if (section) { if (section[1] !== title) heading ||= shorten(section[1]); continue; }
    if (/^([-*_])(?:\s*\1){2,}$/.test(line)) continue;
    const plain = line.replace(/^(?:[-*+>]\s+|\d+\.\s+)/, '').replace(/\*\*|[`]/g, '').trim();
    if (plain) paragraphs.push(shorten(plain));
    if (paragraphs.length === 2) break;
  }
  return paragraphs.length ? paragraphs : (heading || fallback) ? [heading || fallback] : [];
}
