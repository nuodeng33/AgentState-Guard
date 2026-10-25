import { useId, type ReactNode } from 'react';

/** User-selectable agent tints. Tint is preference only; status is always text. */
export const AGENT_TINTS = ['#7cc6bc', '#b9a6f4', '#8fb8e8', '#e3a5bf', '#d9c08a', '#a8c98f'];

function hash(text: string) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** A closed, slightly irregular outline; deterministic per seed so it never jitters. */
function blobPath(seed: string, size: number, wobble = 0.035) {
  const points = 9, c = size / 2, base = size / 2 - 3;
  let h = hash(seed);
  const radii = Array.from({ length: points }, () => { h = Math.imul(h ^ (h >>> 13), 1274126177) >>> 0; return base * (1 - wobble + (h % 1000) / 1000 * wobble * 2); });
  const at = (i: number) => { const a = (i / points) * Math.PI * 2 - Math.PI / 2, r = radii[(i + points) % points]; return [c + Math.cos(a) * r, c + Math.sin(a) * r]; };
  let d = '';
  for (let i = 0; i < points; i++) {
    const [p0, p1, p2, p3] = [at(i - 1), at(i), at(i + 1), at(i + 2)];
    if (!i) d += `M${p1[0].toFixed(1)} ${p1[1].toFixed(1)}`;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6], c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += `C${c1[0].toFixed(1)} ${c1[1].toFixed(1)} ${c2[0].toFixed(1)} ${c2[1].toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  return d + 'Z';
}

/** Soap-film membrane for an agent. Text sits on top and is never deformed. */
export function AgentFilm({ seed, size, tint, state }: { seed: string; size: number; tint: string; state: 'current' | 'absent' | 'unknown' }) {
  const id = useId().replace(/:/g, '');
  const d = blobPath(seed, size);
  return (
    <svg className={`work-film is-${state}`} viewBox={`0 0 ${size} ${size}`} width={size} height={size} aria-hidden="true">
      <defs>
        <radialGradient id={`${id}f`} cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor={tint} stopOpacity=".03" /><stop offset=".68" stopColor={tint} stopOpacity=".06" />
          <stop offset=".92" stopColor={tint} stopOpacity=".16" /><stop offset="1" stopColor={tint} stopOpacity=".26" />
        </radialGradient>
        <linearGradient id={`${id}i`} gradientTransform="rotate(35 .5 .5)">
          <stop offset="0" stopColor={tint} /><stop offset=".3" stopColor="#ebb6d7" /><stop offset=".55" stopColor="#b6e1f0" />
          <stop offset=".78" stopColor="#eedca2" /><stop offset="1" stopColor={tint} />
        </linearGradient>
        <radialGradient id={`${id}h`}><stop offset="0" stopColor="#fff" stopOpacity=".32" /><stop offset="1" stopColor="#fff" stopOpacity="0" /></radialGradient>
        <clipPath id={`${id}c`}><path d={d} /></clipPath>
        <filter id={`${id}b`} x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation={size / 46} /></filter>
      </defs>
      <path d={d} fill="rgba(16,19,21,.28)" />
      <path d={d} fill={`url(#${id}f)`} />
      {state !== 'unknown' && <g clipPath={`url(#${id}c)`}><path d={d} fill="none" stroke={`url(#${id}i)`} strokeWidth={size / 13} strokeOpacity=".5" filter={`url(#${id}b)`} /></g>}
      <ellipse cx={size * 0.34} cy={size * 0.27} rx={size * 0.13} ry={size * 0.06} transform={`rotate(-32 ${size * 0.34} ${size * 0.27})`} fill={`url(#${id}h)`} />
      <path d={d} fill="none" stroke={tint} strokeOpacity={state === 'current' ? 0.7 : 0.4} strokeWidth="1.1" strokeDasharray={state === 'unknown' ? '3 4' : undefined} />
    </svg>
  );
}

/** Hollow thin-shell core: the work is the weight of the space, highlight kept quiet. */
export function WorkCore({ size }: { size: number }) {
  const id = useId().replace(/:/g, '');
  const c = size / 2, r = c - 4;
  const arc = (from: number, to: number, radius: number) => {
    const p = (a: number) => [c + Math.cos(a) * radius, c + Math.sin(a) * radius];
    const [x1, y1] = p(from), [x2, y2] = p(to);
    return `M${x1.toFixed(1)} ${y1.toFixed(1)}A${radius} ${radius} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}`;
  };
  return (
    <svg className="work-core-shell" viewBox={`0 0 ${size} ${size}`} width={size} height={size} aria-hidden="true">
      <defs>
        <radialGradient id={`${id}s`}>
          <stop offset="0" stopColor="#0d0f10" stopOpacity=".42" /><stop offset=".9" stopColor="#0d0f10" stopOpacity=".2" />
          <stop offset=".9" stopColor="#d3b97e" stopOpacity=".05" /><stop offset=".965" stopColor="#d3b97e" stopOpacity=".16" />
          <stop offset="1" stopColor="#d3b97e" stopOpacity=".06" />
        </radialGradient>
      </defs>
      <circle cx={c} cy={c} r={r} fill={`url(#${id}s)`} />
      <circle cx={c} cy={c} r={r} fill="none" stroke="var(--work-shell)" strokeWidth="1.5" />
      <circle cx={c} cy={c} r={r - size * 0.032} fill="none" stroke="var(--work-shell-inner)" strokeWidth="1" />
      <path d={arc(Math.PI * 1.08, Math.PI * 1.42, r - 2)} fill="none" stroke="#f3e4be" strokeOpacity=".4" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

const ICONS: Record<string, ReactNode> = {
  back: <path d="M12.5 4.5 7 10l5.5 5.5" />,
  more: <><circle cx="5" cy="10" r="1.1" fill="currentColor" /><circle cx="10" cy="10" r="1.1" fill="currentColor" /><circle cx="15" cy="10" r="1.1" fill="currentColor" /></>,
  board: <><rect x="3.5" y="4" width="6" height="5" rx="1.2" /><rect x="10.5" y="4" width="6" height="8" rx="1.2" /><rect x="3.5" y="10.5" width="6" height="5.5" rx="1.2" /></>,
  link: <><path d="M8.5 11.5 11.5 8.5" /><path d="M9.5 6.2 11 4.7a3 3 0 0 1 4.3 4.3l-1.5 1.5" /><path d="M10.5 13.8 9 15.3A3 3 0 0 1 4.7 11l1.5-1.5" /></>,
  minus: <path d="M5 10h10" />,
  plus: <path d="M10 5v10M5 10h10" />,
  fit: <path d="M4 7.5V4h3.5M12.5 4H16v3.5M16 12.5V16h-3.5M7.5 16H4v-3.5" />,
  save: <><path d="M5 4h8l3 3v9H5z" /><path d="M7.5 4v3.5h4V4M7.5 16v-4.5h5V16" /></>,
  agents: <><circle cx="7.5" cy="8" r="3.5" /><circle cx="13.5" cy="12" r="3" /></>,
  orbit: <><circle cx="10" cy="10" r="2.5" /><ellipse cx="10" cy="10" rx="7.5" ry="3.5" transform="rotate(-20 10 10)" /></>,
  palette: <><path d="M10 3.5a6.5 6.5 0 1 0 0 13h1a1.8 1.8 0 0 0 1.5-2.8c-.5-.8-.1-1.7.8-1.7h1.2A2.5 2.5 0 0 0 17 9.5a6.4 6.4 0 0 0-7-6Z" /><circle cx="6.5" cy="9" r=".7" /><circle cx="9" cy="6.7" r=".7" /><circle cx="12.5" cy="7" r=".7" /></>,
  environment: <><rect x="4" y="4.5" width="12" height="9" rx="1.5" /><path d="M7.5 16h5" /></>,
  settings: <><circle cx="10" cy="10" r="2.5" /><path d="M10 3.5v2M10 14.5v2M3.5 10h2M14.5 10h2M5.4 5.4l1.4 1.4M13.2 13.2l1.4 1.4M5.4 14.6l1.4-1.4M13.2 6.8l1.4-1.4" /></>,
  expand: <path d="M11.5 4H16v4.5M16 4l-5.5 5.5M8.5 16H4v-4.5M4 16l5.5-5.5" />,
  close: <path d="M5.5 5.5l9 9M14.5 5.5l-9 9" />,
  task: <><rect x="4.5" y="3.5" width="11" height="13" rx="1.5" /><path d="M7.5 7.5h5M7.5 10.5h5M7.5 13.5h3" /></>,
  artifact: <><path d="M5.5 3.5h6l3 3v10h-9z" /><path d="M11.5 3.5v3h3" /></>,
  requirement: <><circle cx="10" cy="10" r="6.5" /><circle cx="10" cy="10" r="2.5" /></>,
  library: <><path d="M4.5 4v12M8 4v12" /><path d="m11 4.5 3.5 11" /></>,
  new: <path d="M10 4.5v11M4.5 10h11" />,
};

export function Icon({ name, size = 18 }: { name: keyof typeof ICONS | string; size?: number }) {
  return <svg className="work-icon" width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICONS[name]}</svg>;
}

/** Inline markup: `code` and **strong** only. Everything else stays literal text. */
function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean).map((part, i) =>
    part.startsWith('`') && part.endsWith('`') && part.length > 2 ? <code key={i}>{part.slice(1, -1)}</code> :
      part.startsWith('**') && part.endsWith('**') && part.length > 4 ? <strong key={i}>{part.slice(2, -2)}</strong> : part);
}

/**
 * Small Markdown subset rendered as React elements (never innerHTML), so body
 * text such as <script> is displayed literally and never executed.
 */
export function Prose({ text }: { text: string }) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const out: ReactNode[] = [];
  for (let i = 0; i < lines.length;) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.startsWith('```')) {
      const code: string[] = []; i++;
      while (i < lines.length && !lines[i].startsWith('```')) code.push(lines[i++]);
      i++; out.push(<pre key={out.length}><code>{code.join('\n')}</code></pre>); continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) { const Tag = (['h3', 'h4', 'h5'] as const)[heading[1].length - 1]; out.push(<Tag key={out.length}>{inline(heading[2])}</Tag>); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const start = i;
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        if (!/^\s*\|[\s:|-]+\|\s*$/.test(lines[i])) rows.push(lines[i].trim().slice(1, -1).split('|').map(cell => cell.trim()));
        i++;
      }
      const [head, ...body] = rows;
      if (!head) {
        out.push(<p key={out.length}>{lines.slice(start, i).join('\n')}</p>);
        continue;
      }
      out.push(<div className="work-table" key={out.length}><table><thead><tr>{head.map((cell, j) => <th key={j}>{inline(cell)}</th>)}</tr></thead>
        <tbody>{body.map((row, r) => <tr key={r}>{row.map((cell, j) => <td key={j}>{inline(cell)}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    const bullet = /^\s*[-*]\s+/, numbered = /^\s*\d+[.)]\s+/;
    if (bullet.test(line) || numbered.test(line)) {
      const pattern = bullet.test(line) ? bullet : numbered, items: string[] = [];
      while (i < lines.length && pattern.test(lines[i])) items.push(lines[i++].replace(pattern, ''));
      const List = pattern === bullet ? 'ul' : 'ol';
      out.push(<List key={out.length}>{items.map((item, j) => <li key={j}>{inline(item)}</li>)}</List>);
      continue;
    }
    out.push(<p key={out.length}>{inline(line)}</p>); i++;
  }
  return <>{out}</>;
}

/** First readable sentence of a requirement, for the core's goal line. */
export function goalOf(requirement: string | undefined) {
  const line = (requirement ?? '').split('\n').map(l => l.trim()).find(l => l && !l.startsWith('#') && !l.startsWith('|'));
  return (line ?? '').replace(/^[-*]\s+|\*\*|`/g, '').slice(0, 90);
}
