import { createRef } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import WorkDesk from './WorkDesk';
import type { WorkArtifactEntry } from './workContentTypes';

const archive: WorkArtifactEntry = { id: 'archive', title: 'Earlier notes', version: 7, requirement_version: 1, created_at: '2026-11-18T18:00:00+08:00' };
const recent: WorkArtifactEntry = { id: 'recent', title: 'Opening script', version: 1, requirement_version: 1, created_at: '2026-11-18T12:00:00Z' };
const work = { id: 'show', title: 'Podcast opening', owner: 'Production',
  requirement: 'Move the host entrance to second three.', requirement_version: 2,
  tasks: [{ id: 'sound', title: 'Check sound cues' }], artifacts: [archive, recent] };

afterEach(cleanup);

it('reads only the newest artifact on entry and peeks at other versions on demand', () => {
  const onOpen = vi.fn();
  const previewArtifact = vi.fn((entry: WorkArtifactEntry, mode?: 'excerpt') => <p>
    {entry.id === recent.id && mode === 'excerpt' ? 'The host starts at second six.' : 'Selected original content'}
  </p>);
  render(<WorkDesk work={work} canOpen contents={{ open: true, allTasks: false, allArtifacts: false }}
    onContents={vi.fn()} continueButton={createRef<HTMLButtonElement>()} onContinue={vi.fn()}
    onOpen={onOpen} previewArtifact={previewArtifact} onCreate={vi.fn()} />);

  const requirement = screen.getByRole('button', { name: 'Open Podcast opening' });
  expect(within(requirement).getByText('v2')).toBeTruthy();
  expect(screen.getByText('The host starts at second six.')).toBeTruthy();
  expect(previewArtifact.mock.calls).toEqual([[recent, 'excerpt']]);
  expect(screen.getByRole('button', { name: 'Opening script v1' })).toBeTruthy();
  fireEvent.click(within(screen.getByRole('region', { name: 'Artifacts' })).getByRole('button', { name: 'From requirement v1' }));
  expect(onOpen).toHaveBeenCalledWith({ kind: 'requirement', ref_id: 'show', version: 1 }, 'origin:artifact:recent:1');

  const archived = screen.getByRole('button', { name: 'Earlier notes v7' });
  fireEvent.keyDown(archived, { key: ' ' });
  expect(previewArtifact).toHaveBeenCalledWith(archive);
  const reading = screen.getByRole('region', { name: 'Earlier notes' });
  expect(within(reading).getByText('Selected original content')).toBeTruthy();
  fireEvent.keyDown(reading, { key: 'Escape' });
  expect(screen.queryByRole('region', { name: 'Earlier notes' })).toBeNull();
  expect(document.activeElement).toBe(archived);
});
