import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const html = await readFile(new URL('./aliyun-product-guide.html', import.meta.url), 'utf8');

test('Issue #12: standalone Chinese HTML declares encoding and mobile viewport', () => {
  assert.match(html, /<!doctype html>/i);
  assert.match(html, /<html lang="zh-CN">/);
  assert.match(html, /<meta charset="utf-8">/);
  assert.match(html, /name="viewport" content="width=device-width, initial-scale=1"/);
  assert.doesNotMatch(html, /<(?:script|link|img|iframe|form|object|embed|video|audio|source)\b/i);
  assert.doesNotMatch(html, /url\s*\(|@import/i);
});

test('Issue #12: every internal link resolves to one unique target', () => {
  const identifiers = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(identifiers.length, new Set(identifiers).size);
  const anchors = [...html.matchAll(/href="([^"]+)"/g)].map(match => match[1]);
  assert.ok(anchors.length >= 5);
  for (const anchor of anchors) {
    assert.ok(anchor.startsWith('#'));
    assert.ok(identifiers.includes(anchor.slice(1)), `Missing target: ${anchor}`);
  }
  const navigation = html.match(/<nav\b[^>]*>([\s\S]*?)<\/nav>/)[1];
  for (const product of ['ecs', 'oss', 'rds']) {
    assert.match(navigation, new RegExp(`href="#${product}"`));
  }
});

test('Issue #12: each product explains its role, scenarios and boundaries', () => {
  const expected = {
    ecs: ['云服务器', '计算实例', '网站与 API', '适用场景', '选型提醒'],
    oss: ['对象存储', '非结构化', '图片与视频', '适用场景', '选型提醒'],
    rds: ['云数据库', '关系型', '订单与用户', '适用场景', '选型提醒'],
  };
  for (const [product, terms] of Object.entries(expected)) {
    const section = html.match(new RegExp(`<section[^>]*id="${product}"[^>]*>([\\s\\S]*?)<\\/section>`))?.[1];
    assert.ok(section, `${product} section must exist`);
    for (const term of terms) assert.ok(section.includes(term), `${product}: ${term}`);
    assert.equal([...section.matchAll(/<li>/g)].length, 3);
  }
});

test('Issue #12: footer explicitly disclaims official affiliation', () => {
  const footer = html.match(/<footer>([\s\S]*?)<\/footer>/)[1];
  assert.match(footer, /非官方演示/);
  assert.match(footer, /与阿里云官方无隶属关系/);
});
