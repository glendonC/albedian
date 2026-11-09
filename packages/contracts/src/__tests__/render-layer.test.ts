import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  ALWAYS_SIMULATED_RENDER_LAYER_KINDS,
  COMPUTED_ONLY_RENDER_LAYER_KINDS,
  EvidenceClassSchema,
  RENDER_LAYER_ID_PATTERN,
  RENDER_LAYER_LIMITS,
  RenderLayerKindSchema,
  RenderLayerSchema,
  RenderLayerSetSchema,
} from '../index.js';

// Written by tests/integration/test_render_layer_contract.py from the Pydantic model
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/render_layer_contract.json');
// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

interface RenderLayerFixture {
  kinds: string[];
  evidenceClasses: string[];
  alwaysSimulatedKinds: string[];
  computedOnlyKinds: string[];
  limits: typeof RENDER_LAYER_LIMITS & { idPattern: string };
  valid: Record<string, unknown>[];
  invalid: { rule: string; layer: Record<string, unknown> }[];
  invalidSets: { rule: string; layers: Record<string, unknown>[] }[];
}

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as RenderLayerFixture;

describe('@albedian/contracts: render layers', () => {
  it('matches the Pydantic model on kinds, evidence classes, rules and limits', () => {
    expect(fixture.kinds).toEqual(RenderLayerKindSchema.options);
    expect(fixture.evidenceClasses).toEqual(EvidenceClassSchema.options);
    expect(fixture.alwaysSimulatedKinds).toEqual(ALWAYS_SIMULATED_RENDER_LAYER_KINDS);
    expect(fixture.computedOnlyKinds).toEqual(COMPUTED_ONLY_RENDER_LAYER_KINDS);
    const { idPattern, ...limits } = fixture.limits;
    expect(limits).toEqual(RENDER_LAYER_LIMITS);
    expect(idPattern).toBe(RENDER_LAYER_ID_PATTERN.source);
  });

  it('parses every layer the Python side wrote, unchanged', () => {
    expect(fixture.valid.length).toBeGreaterThan(0);
    for (const layer of fixture.valid) {
      const parsed = RenderLayerSchema.safeParse(layer);
      expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(true);
      expect(parsed.data).toEqual(layer);
    }
    expect(RenderLayerSetSchema.safeParse(fixture.valid).success).toBe(true);
  });

  it('rejects every layer the Python side rejects', () => {
    for (const { rule, layer } of fixture.invalid) {
      expect(RenderLayerSchema.safeParse(layer).success, rule).toBe(false);
    }
    for (const { rule, layers } of fixture.invalidSets) {
      expect(RenderLayerSetSchema.safeParse(layers).success, rule).toBe(false);
    }
  });

  it('PBT-LAYER-1: the schema accepts exactly the layers the contract rules allow', () => {
    const textOf = (chars: string, minLength: number, maxLength: number) =>
      fc.stringOf(fc.constantFrom(...chars), { minLength, maxLength }).filter((s) => s.trim().length > 0);
    const validFields = fc.record(
      {
        id: fc.stringMatching(RENDER_LAYER_ID_PATTERN),
        kind: fc.constantFrom(...RenderLayerKindSchema.options),
        title: textOf('abcXYZ ', 1, RENDER_LAYER_LIMITS.titleMaxLength),
        evidenceClass: fc.constantFrom(...EvidenceClassSchema.options),
        disclosure: textOf('ab ·.', 1, RENDER_LAYER_LIMITS.disclosureMaxLength),
        sources: fc.array(textOf('ab ', 1, 8), { maxLength: RENDER_LAYER_LIMITS.maxSources }),
        sourceAssetId: textOf('asset-0123', 1, 6),
        observationSolutionId: textOf('sol-0123', 1, 6),
      },
      { requiredKeys: ['id', 'kind', 'title', 'evidenceClass', 'disclosure', 'sources'] }
    );
    const faults: Record<string, fc.Arbitrary<unknown>> = {
      id: fc.constantFrom('', 'A', 'a..b', '-a', ' a'),
      kind: fc.constant('hologram'),
      title: fc.constantFrom('', '   ', 't'.repeat(RENDER_LAYER_LIMITS.titleMaxLength + 1)),
      evidenceClass: fc.constant('artistic'),
      disclosure: fc.constantFrom('', ' ', 'd'.repeat(RENDER_LAYER_LIMITS.disclosureMaxLength + 1)),
      sources: fc.constantFrom([' '], Array.from({ length: RENDER_LAYER_LIMITS.maxSources + 1 }, () => 's')),
      sourceAssetId: fc.constant(''),
      observationSolutionId: fc.constant(''),
    };
    // Valid fields, then on a coin flip (freq 2) one field replaced by a malformed value; the evidence
    // rules are left to chance so well-formed layers that still break a rule come up too
    const layerArb = fc
      .tuple(validFields, fc.option(fc.constantFrom(...Object.keys(faults)), { nil: undefined, freq: 2 }))
      .chain(([layer, faultField]) =>
        faultField === undefined
          ? fc.constant<Record<string, unknown>>(layer)
          : faults[faultField]!.map((bad) => ({ ...layer, [faultField]: bad }))
      );

    // The contract rules restated independently of the schema, as the property's oracle
    const expectedValid = (layer: Record<string, unknown>): boolean => {
      const str = (key: string) => (typeof layer[key] === 'string' ? (layer[key] as string) : null);
      const within = (s: string | null, max: number) => s !== null && s.trim().length >= 1 && s.trim().length <= max;
      const sources = layer.sources as unknown[];
      const textOk =
        RENDER_LAYER_ID_PATTERN.test(str('id') ?? ' ') &&
        within(str('title'), RENDER_LAYER_LIMITS.titleMaxLength) &&
        within(str('disclosure'), RENDER_LAYER_LIMITS.disclosureMaxLength) &&
        sources.length <= RENDER_LAYER_LIMITS.maxSources &&
        sources.every((s) => within(typeof s === 'string' ? s : null, RENDER_LAYER_LIMITS.sourceMaxLength)) &&
        ['sourceAssetId', 'observationSolutionId'].every((k) => !(k in layer) || (str(k) ?? '').length >= 1);
      const kind = str('kind') ?? '';
      const evidence = str('evidenceClass') ?? '';
      if (!textOk || !(RenderLayerKindSchema.options as string[]).includes(kind)) return false;
      if (!(EvidenceClassSchema.options as string[]).includes(evidence)) return false;
      if ((evidence === 'observed' || evidence === 'catalog_matched') && !('sourceAssetId' in layer)) return false;
      if (evidence === 'catalog_matched' && !('observationSolutionId' in layer)) return false;
      if ((COMPUTED_ONLY_RENDER_LAYER_KINDS as string[]).includes(kind) && evidence !== 'predicted' && evidence !== 'simulated') {
        return false;
      }
      return !((ALWAYS_SIMULATED_RENDER_LAYER_KINDS as string[]).includes(kind) && evidence !== 'simulated');
    };
    // A computed overlay is predicted or simulated, never measured; seeded so every run meets each case
    const overlay = (evidenceClass: string, ids: Record<string, string> = {}) => ({
      id: 'sky.horizon',
      kind: 'sky_overlay',
      title: 'Horizon',
      evidenceClass,
      disclosure: 'Computed.',
      sources: [],
      ...ids,
    });

    let accepted = 0;
    let rejected = 0;
    fc.assert(
      fc.property(layerArb, (layer) => {
        const parsed = RenderLayerSchema.safeParse(layer);
        expect(parsed.success).toBe(expectedValid(layer));
        if (parsed.success) {
          accepted++;
          // Round trip: the parsed layer is already in wire form and parses back to itself
          expect(RenderLayerSchema.parse(parsed.data)).toEqual(parsed.data);
        } else {
          rejected++;
        }
      }),
      {
        numRuns: 300,
        seed: SEED,
        examples: [
          [overlay('predicted')],
          [overlay('observed', { sourceAssetId: 'a' })],
          [overlay('catalog_matched', { sourceAssetId: 'a', observationSolutionId: 's' })],
        ],
      }
    );
    // Both outcomes must actually be exercised for the property to mean anything
    expect(accepted).toBeGreaterThan(30);
    expect(rejected).toBeGreaterThan(30);
  });

  it('rejects a repeated id within one scene', () => {
    const layer = fixture.valid[0]!;
    expect(RenderLayerSetSchema.safeParse([layer, { ...layer, title: 'Again' }]).success).toBe(false);
  });
});
