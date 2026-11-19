import type { AnswerStatement, ObservationSolution } from '@albedian/contracts';
import { formatSignificant } from '../components/scanScales.js';

/**
 * The small line beside a statement's evidence tag (ask, approved 2026-11-12: "7.68 parsecs · Hipparcos 2007
 * parallax"): its value in its stored unit written out, then what it rests on. Only the kind the approved picture
 * words has one: a distance in parsecs read from a star's catalog row, measured by its parallax. Every other statement
 * shows its tag alone until the design lane words its unit and source; nothing is written that the statement and the
 * row it names do not carry.
 */
export function statementLine(statement: AnswerStatement, solution: ObservationSolution | undefined): string | null {
  // The path names the row; under the contract that row holds a distance in parsecs
  if (statement.value.kind !== 'number') return null;
  if (statement.subject.kind !== 'finding' || statement.source.kind !== 'solution_field') return null;
  const [root, index, ref, field, part] = statement.source.path;
  if (root !== 'findings' || typeof index !== 'number' || ref !== 'catalogRef' || field !== 'bailerJonesDistancePc' || part !== 'median') return null;
  const finding = solution?.findings[index];
  if (!finding || finding.id !== statement.subject.findingId) return null;
  const row = finding.catalogRef;
  if (row?.bailerJonesDistancePc?.method !== 'geometric_parallax') return null;
  return `${formatSignificant(statement.value.amount)} parsecs · ${row.releaseVersion} parallax`;
}
