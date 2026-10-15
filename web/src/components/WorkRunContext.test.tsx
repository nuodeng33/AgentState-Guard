import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import WorkRunContext from './WorkRunContext';

afterEach(cleanup);
describe('explicit execution context', () => {
  it('loads exact review and file references only on request', async () => {
    const get = vi.fn(async (path: string) => path.includes('/submissions') ? {
      items: [{ reviews: [{ id: 'review-one', decision: 'RETURN', body: 'Fix error handling' }] }], has_more: false,
    } : { items: [{ id: 'version-one', files: [{ file_id: 'file-one', relative_path: 'src/main.ts', category: 'restorable' }] }] });
    const onChange = vi.fn();
    render(<WorkRunContext client={{ get } as unknown as ApiClient} workId="work" artifacts={[]} selected={[]}
      disabled={false} onChange={onChange} fileResult={{ status: 'AVAILABLE', scope_id: 'scope', version_id: 'version-one' }} />);
    expect(get).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText(/Context to send/));
    fireEvent.click(screen.getByRole('button', { name: 'Load review feedback' }));
    fireEvent.click(await screen.findByLabelText(/Review.*RETURN.*Fix error handling/));
    expect(onChange).toHaveBeenLastCalledWith([{ label: expect.any(String), ref: { kind: 'review', ref_id: 'review-one' } }]);
    fireEvent.click(screen.getByRole('button', { name: 'Load inspected run file version' }));
    fireEvent.click(await screen.findByLabelText(/src\/main.ts/));
    expect(onChange).toHaveBeenLastCalledWith([{ label: expect.any(String),
      ref: { kind: 'file', ref_id: 'scope', version_id: 'version-one', file_id: 'file-one' } }]);
  });
});
