import React from 'react';
import {
  ObservationSession,
  ProcessingAttempt,
  ProcessingGraph,
  computeNodeCacheKeys,
  validateProcessingGraphDAG,
} from '@albedian/contracts';

interface ProcessingGraphModalProps {
  session: ObservationSession;
  onClose: () => void;
  onSelectVariant: (variantId: string) => void;
}

/**
 * Inspectable Processing Graph & Provenance Overlay (Section D6 of Media Restoration Research).
 * Kept hidden by default so newcomers see media first; when opened, exposes the real
 * content-addressed DAG, node parameters, SHA-256 cache keys, and evaluation metrics.
 */
export const ProcessingGraphModal: React.FC<ProcessingGraphModalProps> = ({
  session,
  onClose,
  onSelectVariant,
}) => {
  const activeVariant =
    session.mediaVariants.find((v) => v.id === session.activeVariantId) ??
    session.mediaVariants[0]!;
  const activeGraph: ProcessingGraph =
    session.processingGraphs.find((g) => g.id === activeVariant.graphId) ??
    session.processingGraphs[0]!;
  const activeAttempt: ProcessingAttempt | undefined =
    session.processingAttempts.find((a) => a.id === activeVariant.attemptId);

  const dagValidation = validateProcessingGraphDAG(activeGraph);
  const nodeCacheKeys = dagValidation.valid
    ? computeNodeCacheKeys(activeGraph)
    : {};

  return (
    <div
      role="dialog"
      aria-label="Processing Graph & Provenance Inspector"
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(6, 9, 13, 0.86)',
        zIndex: 50,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
      }}
    >
      <div
        style={{
          width: 'min(920px, 95vw)',
          maxHeight: '86vh',
          backgroundColor: 'var(--alb-space-1)',
          border: '1px solid var(--alb-border-subtle)',
          borderRadius: 6,
          padding: 24,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            borderBottom: '1px solid var(--alb-border-subtle)',
            paddingBottom: 12,
          }}
        >
          <div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>
              Media Experiment Runtime · Processing Graph
            </div>
            <div
              className="alb-tabular"
              style={{ fontSize: 12, color: 'var(--alb-text-secondary)', marginTop: 4 }}
            >
              Immutable Source SHA-256: {session.sourceAsset.sha256.slice(0, 20)}… · DAG Status:{' '}
              {dagValidation.valid ? 'Acyclic & Verified' : 'Invalid'}
            </div>
          </div>
          <button type="button" className="alb-btn" onClick={onClose}>
            Back to Media (Esc)
          </button>
        </div>

        {/* Branch Selector */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {session.mediaVariants.map((variant) => (
            <button
              key={variant.id}
              type="button"
              className={`alb-btn ${
                variant.id === session.activeVariantId ? 'alb-btn-active' : ''
              }`}
              onClick={() => onSelectVariant(variant.id)}
            >
              {variant.label}
            </button>
          ))}
        </div>

        {/* Fidelity Policy Guardrails */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
            gap: 10,
            padding: 12,
            backgroundColor: 'var(--alb-space-0)',
            border: '1px solid var(--alb-border-subtle)',
            borderRadius: 4,
          }}
        >
          <div>
            <div style={{ fontSize: 11, color: 'var(--alb-text-muted)' }}>
              Synthetic Sky Generation
            </div>
            <div className="alb-tabular" style={{ fontSize: 13, color: 'var(--alb-accent-catalog)' }}>
              Disabled (Strict Fidelity)
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--alb-text-muted)' }}>
              Observed Star Protection
            </div>
            <div className="alb-tabular" style={{ fontSize: 13 }}>
              {activeGraph.fidelityPolicy.protectObservedStarPoints ? 'Enforced' : 'Off'}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--alb-text-muted)' }}>
              Human Subject Protection
            </div>
            <div className="alb-tabular" style={{ fontSize: 13 }}>
              {activeGraph.fidelityPolicy.preserveFacesAndPeople ? 'Enforced' : 'Off'}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--alb-text-muted)' }}>
              Original Audio Track
            </div>
            <div className="alb-tabular" style={{ fontSize: 13 }}>
              {activeGraph.fidelityPolicy.preserveOriginalAudio
                ? 'Untouched Stream Copy'
                : 'Modified'}
            </div>
          </div>
        </div>

        {/* Topological DAG Node Chain */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--alb-text-secondary)' }}>
            Topological Execution Nodes ({activeGraph.nodes.length})
          </div>
          {activeGraph.nodes.map((node, index) => (
            <div
              key={node.nodeId}
              style={{
                padding: 12,
                backgroundColor: 'var(--alb-surface-raised)',
                border: '1px solid var(--alb-border-subtle)',
                borderRadius: 4,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 16,
              }}
            >
              <div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>
                  {index + 1}. {node.nodeId}{' '}
                  <span
                    className="alb-tabular"
                    style={{
                      fontSize: 11,
                      color: 'var(--alb-accent-catalog)',
                      marginLeft: 8,
                    }}
                  >
                    [{node.kind} v{node.operationVersion}]
                  </span>
                </div>
                <div
                  className="alb-tabular"
                  style={{ fontSize: 12, color: 'var(--alb-text-secondary)', marginTop: 4 }}
                >
                  Inputs: {node.inputNodeIds.length > 0 ? node.inputNodeIds.join(', ') : 'Immutable SourceAsset'} ·
                  Parameters: {JSON.stringify(node.parameters)}
                </div>
              </div>
              <div
                className="alb-tabular"
                style={{ fontSize: 11, color: 'var(--alb-text-muted)', textAlign: 'right' }}
              >
                <div>Cache Hash</div>
                <div style={{ color: 'var(--alb-accent-observation)' }}>
                  {(nodeCacheKeys[node.nodeId] ?? '').slice(0, 16)}
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Measured Attempt Metrics */}
        {activeAttempt?.metrics && (
          <div
            style={{
              padding: 14,
              backgroundColor: 'var(--alb-space-0)',
              border: '1px solid var(--alb-border-subtle)',
              borderRadius: 4,
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
              Measured Attempt Evaluation ({activeAttempt.metrics.evaluationMethod})
            </div>
            <div
              className="alb-tabular"
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
                gap: 10,
                fontSize: 12,
              }}
            >
              <div>
                Shadow SNR Gain: <strong>+{activeAttempt.metrics.shadowSnrGainDb.toFixed(1)} dB</strong>
              </div>
              <div>
                Star Point Retention:{' '}
                <strong>{(activeAttempt.metrics.pointSourceRetentionRatio * 100).toFixed(0)}%</strong>
              </div>
              <div>
                False Point Artifacts:{' '}
                <strong>{activeAttempt.metrics.falsePointArtifactCount}</strong>
              </div>
              <div>
                Audio Sync Drift:{' '}
                <strong>{activeAttempt.metrics.audioSyncOffsetMs.toFixed(1)} ms</strong>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
