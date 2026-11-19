import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { CRAFTS } from '../crafts.js';

// retire-1 (the lead's yes, 19 Nov 17:08): pilot-1's three NASA 3D Resources files under public/models/nasa/ are drawn
// by nothing since hangar-1, so they, their three provenance sources and their Credits mapping are gone; NASA 3D
// Resources stays credited through the hangar's own source.
const publicDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../public');
const provenance = JSON.parse(fs.readFileSync(path.join(publicDir, 'models/PROVENANCE.json'), 'utf8')) as {
  sources: { id: string }[];
  files: { path: string; sources?: string[] }[];
};

describe('the retired NASA placeholders', () => {
  it('are no longer served, recorded or drawn', () => {
    expect(fs.existsSync(path.join(publicDir, 'models/nasa'))).toBe(false);
    expect(provenance.files.filter((f) => f.path.startsWith('nasa/'))).toEqual([]);
    expect(provenance.sources.filter((s) => s.id.startsWith('nasa-3d-resources-')).map((s) => s.id)).toEqual([]);
    expect(Object.values(CRAFTS).filter((c) => [c.modelUrl, c.phoneModelUrl, c.pictureUrl].some((u) => u.includes('/models/nasa/')))).toEqual([]);
  });

  it('leave NASA 3D Resources credited through the hangar files built from it', () => {
    expect(provenance.sources.some((s) => s.id === 'nasa-3d-resources')).toBe(true);
    expect(provenance.files.filter((f) => f.path.startsWith('hangar/') && f.sources?.includes('nasa-3d-resources')).length).toBeGreaterThan(0);
  });
});
