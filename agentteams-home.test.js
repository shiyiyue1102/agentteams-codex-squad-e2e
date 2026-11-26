import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const page = readFileSync(resolve(root, 'agentteams-home.html'), 'utf8');
const stylesheet = readFileSync(resolve(root, 'assets/agentteams/site.css'), 'utf8');
const script = readFileSync(resolve(root, 'assets/agentteams/home.js'), 'utf8');

test('离线首页保留语义、内容区块、运行时与全部安装命令', () => {
  assert.match(page, /<html lang="zh-CN">/);
  assert.match(page, /name="viewport"/);
  assert.equal([...page.matchAll(/<h1[\s>]/g)].length, 1);
  for (const label of ['快速开始', 'OpenClaw', 'QwenPaw', 'Hermes', 'DeepSeek Harness', '实验性', 'OpenHuman', 'Codex', 'Matrix 房间', '共享文件存储', 'Higress AI 网关']) {
    assert.ok(page.includes(label), label);
  }
  for (const command of [
    'curl -fsSL https://agentteams.cloud/install.sh | bash',
    'iwr -UseBasicParsing https://agentteams.cloud/install.ps1 | iex',
    'curl -fsSL https://agentteams.cloud/agentteams-dashboard.sh | bash',
  ]) assert.ok(page.includes(command), command);
  assert.equal([...page.matchAll(/data-copy-install/g)].length, 3);
  assert.equal([...page.matchAll(/data-demo-slide/g)].length, 5);
  assert.equal([...page.matchAll(/data-demo-page=/g)].length, 5);
});

test('本页锚点存在且唯一，站外链接为 HTTPS，本地资源完整', () => {
  const identifiers = [...page.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(identifiers.length, new Set(identifiers).size);
  for (const match of page.matchAll(/\b(?:src|href)="([^"]+)"/g)) {
    const target = match[1];
    if (target.startsWith('#')) assert.ok(identifiers.includes(target.slice(1)), target);
    else if (!target.startsWith('https://')) {
      assert.ok(!target.startsWith('/'), target);
      assert.ok(existsSync(resolve(root, target)), target);
    }
  }
  for (const match of stylesheet.matchAll(/url\(([^)]+)\)/g)) {
    assert.ok(existsSync(resolve(root, 'assets/agentteams', match[1])), match[1]);
  }
  for (const identifier of ['main', 'quick-start', 'how-it-works', 'dashboard', 'dashboard-install']) {
    assert.ok(identifiers.includes(identifier));
  }
});

test('离线运行无模块、远程脚本、请求接口及私有任务路径', () => {
  assert.doesNotMatch(page, /type="module"|<script[^>]+src="https?:/);
  assert.doesNotMatch(script, /\bfetch\(|XMLHttpRequest|eval\(|new Function/);
  assert.doesNotMatch(page + stylesheet + script, /\/root\/|shared\/tasks\/|localhost/);
  for (const match of page.matchAll(/<a\b[^>]*target="_blank"[^>]*>/g)) {
    assert.match(match[0], /rel="noopener"/);
  }
  assert.match(script, /prefers-reduced-motion: reduce/);
  assert.match(script, /请手动复制/);
  assert.match(script, /visibilitychange/);
});
