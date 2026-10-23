import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import WorkBubbleGroup from './WorkBubbleGroup';
const props = (select = vi.fn()) => ({ label: 'Linked task actions', width: 150, height: 150,
  actions: [{ id: 'task-a', label: 'Actual task', onSelect: select }],
  renderTrigger: (trigger: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button {...trigger}>Agent</button> });
const group = () => screen.getByRole('button', { name: 'Agent' }).parentElement!;
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
async function reveal() { fireEvent.pointerEnter(group()); return screen.findByRole('button', { name: 'Actual task' }); }
describe('work bubble interactions', () => {
  it('starts hidden and uses a continuous parent-child group without submitting anything', async () => {
    const select = vi.fn(); render(<WorkBubbleGroup {...props(select)} />);
    expect(screen.queryByRole('button', { name: 'Actual task' })).toBeNull();
    const child = await reveal();
    fireEvent.pointerLeave(screen.getByRole('button', { name: 'Agent' }), { relatedTarget: child });
    fireEvent.pointerEnter(child);
    expect(group().className).toContain('is-expanded'); expect(select).not.toHaveBeenCalled();
    fireEvent.click(child); expect(select).toHaveBeenCalledTimes(1);
  });
  it('pins on parent click, survives pointer exit, then unpins', async () => {
    render(<WorkBubbleGroup {...props()} />); await reveal();
    fireEvent.click(screen.getByRole('button', { name: 'Agent' })); fireEvent.pointerLeave(group());
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 100)); });
    expect(screen.getByRole('button', { name: 'Actual task' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Agent' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Agent' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Actual task' })).toBeNull());
  });
  it('keeps exit nodes mounted while retracting but disables access, then removes them', async () => {
    const { container } = render(<WorkBubbleGroup {...props()} />); await reveal();
    fireEvent.pointerLeave(group());
    await waitFor(() => expect(group().className).not.toContain('is-expanded'));
    const child = container.querySelector<HTMLButtonElement>('[data-bubble-child="task-a"]');
    expect(child).toBeTruthy(); expect(child!.disabled).toBe(true); expect(child!.tabIndex).toBe(-1);
    await waitFor(() => expect(container.querySelector('[data-bubble-child="task-a"]')).toBeNull());
  });
  it('Escape closes the group before work navigation and returns focus to the parent', async () => {
    render(<WorkBubbleGroup {...props()} />); const child = await reveal(); act(() => child.focus());
    const navigate = vi.fn(); document.addEventListener('keydown', navigate);
    fireEvent.keyDown(child, { key: 'Escape' });
    expect(navigate).not.toHaveBeenCalled(); expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Agent' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Actual task' })).toBeNull());
    document.removeEventListener('keydown', navigate);
  });
  it('opens on keyboard entry after an earlier pointer interaction', async () => {
    render(<><button>Outside</button><WorkBubbleGroup {...props()} /></>);
    const parent = screen.getByRole('button', { name: 'Agent' });
    await reveal(); fireEvent.pointerDown(parent); fireEvent.pointerLeave(group());
    const outside = screen.getByRole('button', { name: 'Outside' }); act(() => outside.focus());
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Actual task' })).toBeNull());
    fireEvent.keyDown(outside, { key: 'Tab' }); act(() => parent.focus());
    expect(await screen.findByRole('button', { name: 'Actual task' })).toBeTruthy();
    expect(parent.getAttribute('aria-expanded')).toBe('true');
  });
  it('reverses an in-progress collapse on reentry', async () => {
    const { container } = render(<WorkBubbleGroup {...props()} />); await reveal(); fireEvent.pointerLeave(group());
    await waitFor(() => expect(group().className).not.toContain('is-expanded'));
    expect(container.querySelector('[data-bubble-child="task-a"]')).toBeTruthy();
    fireEvent.pointerEnter(group()); await screen.findByRole('button', { name: 'Actual task' });
    expect(group().className).toContain('is-expanded');
  });
});
