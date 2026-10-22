import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { Prose } from './WorkVisuals';

describe('work content rendering', () => {
  it.each(['| --- | --- |', '| :--- | ---: |\n| --- | --- |'])('keeps separator-only content readable: %s', text => {
    const html = renderToStaticMarkup(<Prose text={text} />);
    expect(html).toContain('---');
    expect(html).not.toContain('<table>');
  });
  it('renders bilingual tables while keeping HTML inert', () => {
    const html = renderToStaticMarkup(<Prose text={'| 状态 / State | 说明 |\n| --- | --- |\n| UNKNOWN | <script>alert(1)</script> |'} />);
    expect(html).toContain('<table>');
    expect(html).toContain('状态 / State');
    expect(html).toContain('UNKNOWN');
    expect(html).not.toContain('<script>');
  });
});
