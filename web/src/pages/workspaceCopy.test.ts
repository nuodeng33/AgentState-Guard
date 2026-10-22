import { expect, it } from 'vitest';
import { workspaceCopy } from './workspaceCopy';

it.each([0, 1, 2])('formats English work counts naturally for %i', n => {
  expect(workspaceCopy['en-US'].overviewCount(n)).toContain(`${n} work ${n === 1 ? 'item' : 'items'}`);
  expect(workspaceCopy['en-US'].taskCount(n)).toBe(`${n} ${n === 1 ? 'task' : 'tasks'}`);
  expect(workspaceCopy['en-US'].artifactCount(n)).toBe(`${n} ${n === 1 ? 'artifact' : 'artifacts'}`);
  expect(workspaceCopy['zh-CN'].taskCount(n)).toBe(`${n} 项任务`);
  expect(workspaceCopy['zh-CN'].artifactCount(n)).toBe(`${n} 项成果`);
});
