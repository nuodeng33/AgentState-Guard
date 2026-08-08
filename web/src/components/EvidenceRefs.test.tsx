import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { EvidenceRefs } from './EvidenceRefs';

describe('EvidenceRefs', () => {
  it('renders an explicit marker when there are no refs', () => {
    render(<EvidenceRefs refs={[]} />);
    expect(screen.getByText('No evidence refs')).toBeTruthy();
  });

  it('stays collapsed by default and expands on demand', () => {
    render(<EvidenceRefs refs={['evt-aaa', 'evt-bbb']} />);
    const toggle = screen.getByRole('button', { name: 'Evidence (2)' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText('evt-aaa')).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText('evt-aaa')).toBeTruthy();
    expect(screen.getByText('evt-bbb')).toBeTruthy();
  });

  it('renders references verbatim without deriving paths or commands', () => {
    render(<EvidenceRefs refs={['ledger:evt-1']} />);
    fireEvent.click(screen.getByRole('button'));
    const item = screen.getByText('ledger:evt-1');
    expect(item.textContent).toBe('ledger:evt-1');
  });
});
