import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { increment } from './counter.js';

describe('increment', () => {
  it('adds 1 to zero', () => {
    assert.equal(increment(0), 1);
  });

  it('adds 1 to a positive number', () => {
    assert.equal(increment(5), 6);
  });

  it('adds 1 to a negative number', () => {
    assert.equal(increment(-3), -2);
  });
});
