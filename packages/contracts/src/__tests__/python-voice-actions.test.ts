import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { AlbedianActionSchema } from '../index.js';

// Written by tests/integration/test_voice_claude_path.py: one action per tool the Claude intent path offers
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/python_voice_actions.json');

describe('@albedian/contracts: Python voice action parity', () => {
  const samples = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as Record<string, unknown>;

  it('holds at least one composed action', () => {
    expect(Object.keys(samples).length).toBeGreaterThan(0);
  });

  it.each(Object.keys(samples))('parses the action Python composes for the %s tool, field for field', (tool) => {
    const parsed = AlbedianActionSchema.safeParse(samples[tool]);
    expect(parsed.error?.issues ?? []).toEqual([]);
    // Zod drops unknown keys, so an extra field from Python would only show up here
    expect(parsed.data).toEqual(samples[tool]);
  });
});
