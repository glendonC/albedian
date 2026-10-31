import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  ProcessingGraph,
  ProcessingGraphSchema,
  ProcessingNode,
  SourceAssetSchema,
  computeNodeCacheKeys,
  validateProcessingGraphDAG,
} from '../index.js';

describe('@albedian/contracts — Domain Schemas & PBT Suite #4 (Processing DAG)', () => {
  it('validates immutable SourceAsset schema and rejects mutable flags', () => {
    const validSource = {
      id: 'src_sahara_001',
      title: 'Sahara Night — Erg Chebbi',
      mediaKind: 'video' as const,
      sha256: 'a8f5c2d9104e8b771234567890abcdef',
      uri: 'file://fixtures/sahara_night.mp4',
      width: 1920,
      height: 1080,
      durationMs: 12000,
      fpsNumerator: 30,
      fpsDenominator: 1,
      videoCodec: 'h264',
      audioStreams: [
        {
          index: 1,
          codec: 'aac',
          sampleRateHz: 48000,
          channels: 2,
          startPtsMs: 0,
          durationMs: 12000,
          sha256: 'audio77889900aabb',
        },
      ],
      capturedAtUTC: '2026-10-19T22:14:00Z',
      observerLocation: {
        latDeg: 31.149,
        lonDeg: -3.972,
        elevationM: 740,
        placeName: 'Erg Chebbi, Sahara, Morocco',
      },
      privacyClass: 'consented_demo' as const,
      immutable: true as const,
    };

    expect(SourceAssetSchema.safeParse(validSource).success).toBe(true);
    expect(
      SourceAssetSchema.safeParse({ ...validSource, immutable: false }).success
    ).toBe(false);
  });

  it('PBT-DAG-1: Every generated layered DAG obeys topological dependency ordering and preserves sourceSha256', () => {
    const layeredDagArb = fc
      .record({
        sourceSha256: fc.hexaString({ minLength: 16, maxLength: 32 }),
        extraStageCount: fc.integer({ min: 1, max: 5 }),
        gainParam: fc.double({ min: 0.5, max: 4.0, noNaN: true }),
        denoiseStrength: fc.double({ min: 0.1, max: 1.0, noNaN: true }),
      })
      .map(({ sourceSha256, extraStageCount, gainParam, denoiseStrength }) => {
        const nodes: ProcessingNode[] = [
          {
            nodeId: 'node_0_decode',
            kind: 'source_decode',
            operationVersion: '1.0.0',
            inputNodeIds: [],
            parameters: { colorSpace: 'rec709' },
            preservePointSources: true,
          },
        ];

        const kinds = [
          'tone_transfer',
          'spatial_denoise',
          'temporal_stack',
          'star_protect_merge',
          'encode_mux',
        ] as const;

        for (let i = 1; i <= extraStageCount; i++) {
          const parentIds = [nodes[i - 1]!.nodeId];
          if (i >= 2 && i % 2 === 0) {
            parentIds.push('node_0_decode');
          }
          nodes.push({
            nodeId: `node_${i}_${kinds[(i - 1) % kinds.length]}`,
            kind: kinds[(i - 1) % kinds.length]!,
            operationVersion: '1.0.0',
            inputNodeIds: parentIds,
            parameters: {
              gain: Number(gainParam.toFixed(4)),
              strength: Number(denoiseStrength.toFixed(4)),
              stage: i,
            },
            preservePointSources: true,
          });
        }

        const graph: ProcessingGraph = {
          id: 'graph_pbt',
          sourceAssetId: 'src_01',
          sourceSha256,
          revisionSha256: 'rev_12345678',
          profileName: 'star_protected',
          fidelityPolicy: {
            noSyntheticSceneContent: true,
            allowSyntheticSky: false,
            preserveOriginalAudio: true,
            preserveFacesAndPeople: true,
            protectObservedStarPoints: true,
          },
          nodes,
          createdAtUTC: '2026-10-31T12:00:00Z',
        };
        return graph;
      });

    fc.assert(
      fc.property(layeredDagArb, (graph) => {
        const parsed = ProcessingGraphSchema.safeParse(graph);
        expect(parsed.success).toBe(true);

        const originalSha = graph.sourceSha256;
        const result = validateProcessingGraphDAG(graph);
        expect(result.valid).toBe(true);
        expect(result.topologicalOrder.length).toBe(graph.nodes.length);

        // Verify every node appears strictly after all of its inputNodeIds
        const indexMap = new Map(
          result.topologicalOrder.map((id, idx) => [id, idx])
        );
        for (const node of graph.nodes) {
          const nodeIdx = indexMap.get(node.nodeId)!;
          for (const parentId of node.inputNodeIds) {
            const parentIdx = indexMap.get(parentId)!;
            expect(parentIdx).toBeLessThan(nodeIdx);
          }
        }

        // Verify cache keys are deterministic and sourceSha256 is untouched
        const keys1 = computeNodeCacheKeys(graph);
        const keys2 = computeNodeCacheKeys(graph);
        expect(keys1).toEqual(keys2);
        expect(graph.sourceSha256).toBe(originalSha);
      }),
      { numRuns: 100 }
    );
  });

  it('PBT-DAG-2: Injecting any back-edge cycle into a valid chain is deterministically rejected', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 2, max: 6 }),
        fc.integer({ min: 0, max: 5 }),
        (chainLength, rawTarget) => {
          const targetIdx = rawTarget % (chainLength - 1);
          const sourceIdx = chainLength - 1; // back-edge from last node into targetIdx

          const nodes: ProcessingNode[] = [
            {
              nodeId: 'n0',
              kind: 'source_decode',
              operationVersion: '1.0.0',
              inputNodeIds: [],
              parameters: {},
              preservePointSources: true,
            },
          ];

          for (let i = 1; i < chainLength; i++) {
            nodes.push({
              nodeId: `n${i}`,
              kind: 'spatial_denoise',
              operationVersion: '1.0.0',
              inputNodeIds: [`n${i - 1}`],
              parameters: { step: i },
              preservePointSources: true,
            });
          }

          // Inject cycle: node targetIdx depends on node sourceIdx (>= targetIdx)
          if (targetIdx === 0) {
            // Keep n0 as root decode, add cycle between n1 and n_{chainLength-1}
            if (chainLength >= 3) {
              nodes[1]!.inputNodeIds.push(`n${sourceIdx}`);
            } else {
              nodes[1]!.inputNodeIds.push('n1'); // self-loop
            }
          } else {
            nodes[targetIdx]!.inputNodeIds.push(`n${sourceIdx}`);
          }

          const graph: ProcessingGraph = {
            id: 'graph_cycle',
            sourceAssetId: 'src_01',
            sourceSha256: '0123456789abcdef0123456789abcdef',
            revisionSha256: 'rev_cycle123',
            profileName: 'custom',
            fidelityPolicy: {
              noSyntheticSceneContent: true,
              allowSyntheticSky: false,
              preserveOriginalAudio: true,
              preserveFacesAndPeople: true,
              protectObservedStarPoints: true,
            },
            nodes,
            createdAtUTC: '2026-10-31T12:00:00Z',
          };

          const validation = validateProcessingGraphDAG(graph);
          expect(validation.valid).toBe(false);
          expect(validation.errors.some((e) => e.includes('Cycle'))).toBe(true);
        }
      ),
      { numRuns: 60 }
    );
  });

  it('PBT-DAG-3: Mutating a branch node parameter invalidates downstream cache keys while preserving upstream cache keys', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0.1, max: 2.0, noNaN: true }),
        fc.double({ min: 2.1, max: 5.0, noNaN: true }),
        (valA, valB) => {
          const makeGraph = (denoiseVal: number): ProcessingGraph => ({
            id: 'g1',
            sourceAssetId: 'src_1',
            sourceSha256: 'abcdef0123456789abcdef0123456789',
            revisionSha256: 'rev_12345678',
            profileName: 'natural',
            fidelityPolicy: {
              noSyntheticSceneContent: true,
              allowSyntheticSky: false,
              preserveOriginalAudio: true,
              preserveFacesAndPeople: true,
              protectObservedStarPoints: true,
            },
            nodes: [
              {
                nodeId: 'decode',
                kind: 'source_decode',
                operationVersion: '1.0',
                inputNodeIds: [],
                parameters: { format: 'rgb24' },
                preservePointSources: true,
              },
              {
                nodeId: 'tone',
                kind: 'tone_transfer',
                operationVersion: '1.0',
                inputNodeIds: ['decode'],
                parameters: { gamma: 1.8 },
                preservePointSources: true,
              },
              {
                nodeId: 'denoise',
                kind: 'spatial_denoise',
                operationVersion: '1.0',
                inputNodeIds: ['tone'],
                parameters: { sigma: denoiseVal },
                preservePointSources: true,
              },
              {
                nodeId: 'mux',
                kind: 'encode_mux',
                operationVersion: '1.0',
                inputNodeIds: ['denoise'],
                parameters: { crf: 18 },
                preservePointSources: true,
              },
            ],
            createdAtUTC: '2026-10-31T12:00:00Z',
          });

          const keysA = computeNodeCacheKeys(makeGraph(valA));
          const keysB = computeNodeCacheKeys(makeGraph(valB));

          // Upstream nodes must retain identical cache keys
          expect(keysA['decode']).toBe(keysB['decode']);
          expect(keysA['tone']).toBe(keysB['tone']);

          // Modified node and its descendants must receive new cache keys
          expect(keysA['denoise']).not.toBe(keysB['denoise']);
          expect(keysA['mux']).not.toBe(keysB['mux']);
        }
      ),
      { numRuns: 80 }
    );
  });
});
