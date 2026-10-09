import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../adapters/opencode/bantay.js', import.meta.url), 'utf8');
const { BantayPlugin: plugin } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

test('patch headers without spaces are judged', () => {
  const calls = plugin.translate('apply_patch', { patchText: '*** Begin Patch\n*** Delete File:.opencode/plugin/bantay.js\n*** End Patch' });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].tool_name, 'Bash');
  assert.match(calls[0].tool_input.command, /bantay.js/);
});
test('unknown tools and malformed patch envelopes reach the judge', () => {
  assert.deepEqual(plugin.translate('mcp_delete', {path: '/p'}), [{tool_name: 'mcp_delete', tool_input: {path: '/p'}}]);
  assert.equal(plugin.translate('apply_patch', {patchText: 'unknown syntax'})[0].tool_name, 'apply_patch');
  assert.deepEqual(plugin.translate('todowrite', {}), []);
});
test('ask refuses execution and bash workdir resolves against project', async () => {
  let seen;
  globalThis.fetch = async (_, options) => {
    seen = JSON.parse(options.body);
    return {ok: true, json: async () => ({verdict: 'ask', reason: 'review first'})};
  };
  await assert.rejects(plugin.guard('bash', {command:'npm publish',workdir:'sub'}, {directory:'/project'}), /needs human approval/);
  assert.equal(seen.cwd, '/project/sub');
});
test('unreachable daemon refuses unknown tools', async () => {
  globalThis.fetch = async () => { throw new Error('offline'); };
  await assert.rejects(plugin.guard('custom', {}), /daemon unreachable/);
});
test('list maps to LS and todoread is not judged', () => {
  assert.deepEqual(plugin.translate('list', {path: 'src'}), [{tool_name: 'LS', tool_input: {path: 'src'}}]);
  assert.deepEqual(plugin.translate('todoread', {}), []);
  assert.equal(plugin.translate('task', {prompt: 'x'})[0].tool_name, 'task');
  assert.equal(plugin.translate('skill', {name: 'x'})[0].tool_name, 'skill');
});
test('a block without a reason still explains itself', async () => {
  globalThis.fetch = async () => ({ok: true, json: async () => ({verdict: 'block'})});
  await assert.rejects(plugin.guard('bash', {command: 'x'}), (e) => !/undefined/.test(e.message) && /no reason given/.test(e.message));
});
test('plugin paths are matched case-insensitively', async () => {
  globalThis.fetch = async () => { throw new Error('daemon must not be asked'); };
  await assert.rejects(plugin.guard('write', {filePath: '.OpenCode/Plugin/bantay.js', content: ''}), /Disables the Bantay/);
});
