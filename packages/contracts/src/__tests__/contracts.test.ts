import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  AttemptEvaluationMetricsSchema,
  ExportArtifactSchema,
  MediaVariantSchema,
  ObservationSessionSchema,
  ProcessingAttemptSchema,
  ProcessingGraph,
  ProcessingGraphSchema,
  ProcessingNode,
  SourceAssetSchema,
  computeNodeCacheKeys,
  validateProcessingGraphDAG,
} from '../index.js';

describe('@albedian/contracts: Domain Schemas & PBT Suite #4 (Processing DAG)', () => {
  it('validates immutable SourceAsset schema and rejects mutable flags', () => {
    const validSource = {
      id: 'src_synthetic_001',
      title: 'Synthetic night clip',
      mediaKind: 'video' as const,
      sha256: 'a8f5c2d9104e8b771234567890abcdefa8f5c2d9104e8b771234567890abcdef',
      uri: 'file://fixtures/synthetic_night.mp4',
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
      // A synthetic moment and place that point at nobody: the open North Atlantic
      capturedAtUTC: '2030-01-01T02:00:00Z',
      observerLocation: {
        latDeg: 30.0,
        lonDeg: -40.0,
        elevationM: 0,
        placeName: 'Synthetic place, open North Atlantic',
      },
      metadataSources: {
        capturedAtUTC: { container: 'synthetic' as const, tags: ['test-fixture'] },
        observerLocation: { container: 'synthetic' as const, tags: ['test-fixture'] },
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
          createdAtUTC: '2031-02-17T12:00:00Z',
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
            createdAtUTC: '2031-02-17T12:00:00Z',
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
            createdAtUTC: '2031-02-17T12:00:00Z',
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

  it('accepts restore metrics, attempts, variants and exports with unmeasured fields omitted and rejects null', () => {
    const metricsWithoutUnmeasured = {
      evaluationMethod: 'no_reference_internal_sky_background_mad_and_star_core_flux',
      hasPairedReference: false,
      shadowSnrGainDb: 3.2,
      estimatedNoiseReductionPct: 28.5,
      falsePointArtifactCount: 0,
      validityNotes: ['Single frame: temporal flicker is not defined.', 'No reference stars detected.'],
    };
    expect(AttemptEvaluationMetricsSchema.safeParse(metricsWithoutUnmeasured).success).toBe(true);
    expect(
      AttemptEvaluationMetricsSchema.safeParse({
        ...metricsWithoutUnmeasured,
        pointSourceRetentionRatio: 0.96,
        temporalFlickerIndex: 0.03,
        audioSyncOffsetMs: 0.4,
      }).success
    ).toBe(true);
    expect(
      AttemptEvaluationMetricsSchema.safeParse({ ...metricsWithoutUnmeasured, pointSourceRetentionRatio: -0.01 }).success
    ).toBe(false);
    expect(
      AttemptEvaluationMetricsSchema.safeParse({ ...metricsWithoutUnmeasured, pointSourceRetentionRatio: 1.51 }).success
    ).toBe(false);
    expect(
      AttemptEvaluationMetricsSchema.safeParse({ ...metricsWithoutUnmeasured, temporalFlickerIndex: -0.01 }).success
    ).toBe(false);
    expect(
      AttemptEvaluationMetricsSchema.safeParse({ ...metricsWithoutUnmeasured, pointSourceRetentionRatio: null }).success
    ).toBe(false);
    expect(
      AttemptEvaluationMetricsSchema.safeParse({ ...metricsWithoutUnmeasured, temporalFlickerIndex: null }).success
    ).toBe(false);
    expect(
      AttemptEvaluationMetricsSchema.safeParse({ ...metricsWithoutUnmeasured, audioSyncOffsetMs: null }).success
    ).toBe(false);

    const attemptWithoutPeakMemory = {
      id: 'attempt_01',
      sourceAssetId: 'src_01',
      graphId: 'graph_01',
      label: 'Restoration Branch (star_protected)',
      status: 'ready' as const,
      cacheKey: '0123456789abcdef0123456789abcdef',
      startedAtUTC: '2030-01-01T02:00:00.000Z',
      completedAtUTC: '2030-01-01T02:00:00.250Z',
      elapsedMs: 250,
      cpuMs: 210,
      gpuMs: 0,
      metrics: metricsWithoutUnmeasured,
    };
    expect(ProcessingAttemptSchema.safeParse(attemptWithoutPeakMemory).success).toBe(true);
    expect(
      ProcessingAttemptSchema.safeParse({ ...attemptWithoutPeakMemory, peakMemoryBytes: 67108864 }).success
    ).toBe(true);
    expect(
      ProcessingAttemptSchema.safeParse({ ...attemptWithoutPeakMemory, peakMemoryBytes: -1 }).success
    ).toBe(false);
    expect(
      ProcessingAttemptSchema.safeParse({ ...attemptWithoutPeakMemory, peakMemoryBytes: 1024.5 }).success
    ).toBe(false);
    expect(
      ProcessingAttemptSchema.safeParse({ ...attemptWithoutPeakMemory, peakMemoryBytes: null }).success
    ).toBe(false);

    const variantWithoutAudioOffset = {
      id: 'variant_01',
      sourceAssetId: 'src_01',
      attemptId: 'attempt_01',
      graphId: 'graph_01',
      label: 'Star-Protected Variant',
      uri: 'ni:///sha-256;abcdef0123456789',
      sha256: '0123456789abcdef0123456789abcdef',
      width: 1920,
      height: 1080,
      durationMs: 4000,
      frameTimestampsMs: [0, 1000, 2000, 3000],
      audioStreamCopiedFromSource: false,
      summaryNote: 'Frames only; no audio stream.',
    };
    expect(MediaVariantSchema.safeParse(variantWithoutAudioOffset).success).toBe(true);
    expect(MediaVariantSchema.safeParse({ ...variantWithoutAudioOffset, audioSyncOffsetMs: 0.5 }).success).toBe(true);
    expect(MediaVariantSchema.safeParse({ ...variantWithoutAudioOffset, audioSyncOffsetMs: null }).success).toBe(false);

    const exportWithoutAudioOffset = {
      id: 'export_01',
      sessionId: 'sess_01',
      sourceAssetId: 'src_01',
      sourceSha256: '0123456789abcdef0123456789abcdef',
      exportedVariantId: 'variant_01',
      outputUri: 'ni:///sha-256;f4OxZX_x_FO5LcGBSKHWXfwtSx-j1ncoSt3SABJtkGk',
      outputSha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
      containerFormat: 'mp4' as const,
      videoCodec: 'h264',
      audioPreserved: false,
      processingGraphId: 'graph_01',
      catalogCitations: [],
      epistemicSummary: {
        observedCount: 0,
        catalogMatchedCount: 0,
        predictedCount: 0,
        simulatedViewsIncluded: false,
        strictFidelityEnforced: true,
      },
      exportedAtUTC: '2030-01-01T02:00:01.000Z',
    };
    expect(ExportArtifactSchema.safeParse(exportWithoutAudioOffset).success).toBe(true);
    expect(ExportArtifactSchema.safeParse({ ...exportWithoutAudioOffset, audioSyncOffsetMs: 0.2 }).success).toBe(true);
    expect(
      ExportArtifactSchema.safeParse({ ...exportWithoutAudioOffset, audioSyncOffsetMs: null }).success
    ).toBe(false);
  });

  it('requires ObservationSession to omit sourceAsset and observationSolution when media is empty and include both otherwise', () => {
    const spatialView = {
      mode: 'source_2d' as const,
      transitionPhase: 'at_source' as const,
      transitionProgress: 0,
      observerOriginParsec: [0, 0, 0] as [number, number, number],
      cameraQuaternion: [0, 0, 0, 1] as [number, number, number, number],
      fovVerticalDeg: 60,
      frustumShiftNdc: [0, 0] as [number, number],
      simulationEpochISO: '2030-01-01T02:00:00Z',
      sceneScalePolicy: 'physical_euclidean' as const,
      activeOverlays: {
        observedMarkers: false,
        catalogMatchedLabels: false,
        predictedEphemerides: false,
        constellationLines: false,
        earthDirectionVector: false,
        residualVectors: false,
        sightlineCone: false,
      },
    };
    const anchorMomentCheckpoint = {
      id: 'chk_none',
      label: 'No observation yet',
      createdAtMs: 0,
      sourceAssetId: 'src_none',
      sourceTimeSec: 0,
      playbackWasPlaying: false,
      audioGain: 1,
      mediaVariantId: 'variant_none',
      cropTransform: { zoom: 1, panX: 0, panY: 0 },
      selectedFindingId: null,
      navigationState: 'source' as const,
      spatialView,
      viewRevision: 0,
    };
    const emptySession = {
      id: 'session_empty',
      title: 'No observation yet',
      viewRevision: 0,
      machines: {
        media: 'empty' as const,
        restoration: 'idle' as const,
        calibration: 'not_requested' as const,
        navigation: 'source' as const,
        voice: 'off' as const,
      },
      activeVariantId: 'variant_none',
      compareWipePosition: 0.5,
      currentSourceTimeSec: 0,
      isPlaying: false,
      audioGain: 1,
      cropTransform: { zoom: 1, panX: 0, panY: 0 },
      processingGraphs: [],
      processingAttempts: [],
      mediaVariants: [],
      selectedFindingId: null,
      focusedFindingId: null,
      spatialView,
      navigationHistory: {
        anchorMomentCheckpoint,
        checkpoints: [anchorMomentCheckpoint],
        cursorIndex: 0,
      },
      voiceActions: [],
      exportArtifacts: [],
      reducedMotion: false,
    };
    const sourceAsset = {
      id: 'src_upload_01',
      title: 'night.jpg',
      mediaKind: 'image' as const,
      sha256: 'a8f5c2d9104e8b771234567890abcdefa8f5c2d9104e8b771234567890abcdef',
      uri: 'blob:night',
      width: 1920,
      height: 1080,
      durationMs: 0,
      videoCodec: 'none',
      audioStreams: [],
      privacyClass: 'private' as const,
      immutable: true as const,
    };
    const observationSolution = {
      id: 'sol_upload_01',
      sourceAssetId: sourceAsset.id,
      sourceSha256: sourceAsset.sha256,
      frameTimeSec: 0,
      revision: 1,
      status: 'pending' as const,
      // Nothing has run yet, so a pending solution names no solver and counts nothing
      findings: [],
      warnings: [],
    };

    const parsedEmpty = ObservationSessionSchema.safeParse(emptySession);
    expect(parsedEmpty.error?.issues ?? []).toEqual([]);
    expect(parsedEmpty.success).toBe(true);

    expect(ObservationSessionSchema.safeParse({ ...emptySession, sourceAsset }).success).toBe(false);
    expect(ObservationSessionSchema.safeParse({ ...emptySession, observationSolution }).success).toBe(false);
    expect(
      ObservationSessionSchema.safeParse({ ...emptySession, sourceAsset, observationSolution }).success
    ).toBe(false);

    const importedSession = {
      ...emptySession,
      machines: { ...emptySession.machines, media: 'importing' as const },
      sourceAsset,
      observationSolution,
    };
    expect(ObservationSessionSchema.safeParse(importedSession).success).toBe(true);
    expect(
      ObservationSessionSchema.safeParse({
        ...emptySession,
        machines: { ...emptySession.machines, media: 'importing' as const },
        sourceAsset,
      }).success
    ).toBe(false);
    expect(
      ObservationSessionSchema.safeParse({
        ...emptySession,
        machines: { ...emptySession.machines, media: 'importing' as const },
        observationSolution,
      }).success
    ).toBe(false);
  });
});
