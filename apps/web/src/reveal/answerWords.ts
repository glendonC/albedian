import { sourceRow, type AnswerStatement, type AnswerUnit, type ObservationSolution } from '@albedian/contracts';

/**
 * A value in its stored unit, written out once as C7 decides (UIUX-words-DECIDED-BY-LEAD.md): parsecs, astronomical
 * units, "magnitude {v}" with the word first, kelvin, degrees, arcseconds, pixels, "{v} of 1", a count as the number
 * alone, "{v} times". Three figures, as every number the app shows; a count in full.
 */
export function valueWords(amount: number, unit: AnswerUnit): string {
  // Three figures at most, with no zeros added after the last one: a catalog's 0.03 is not 0.0300
  const v = Number(amount.toPrecision(3)).toLocaleString('en-US', { maximumSignificantDigits: 3 });
  switch (unit) {
    case 'pc':
      return `${v} parsecs`;
    case 'au':
      return `${v} astronomical units`;
    case 'mag':
      return `magnitude ${v}`;
    case 'K':
      return `${v} kelvin`;
    case 'deg':
      return `${v} degrees`;
    case 'arcsec':
      return `${v} arcseconds`;
    case 'px':
      return `${v} pixels`;
    case 'fraction':
      return `${v} of 1`;
    case 'count':
      return Math.round(amount).toLocaleString('en-US');
    case 'ratio':
      return `${v} times`;
  }
}

/**
 * What a statement rests on, in two or three words (C7): a catalog row by its catalog's name, and "parallax" for a
 * distance measured by one ("Hipparcos 2007 parallax"); the ephemeris as "ERFA ephemeris"; the solve as "this photo's
 * solve"; a value measured in the photo as "this photo's pixels". None where no words are decided (a pose's), or where
 * the statement and the row it names disagree.
 */
function sourceWords(statement: AnswerStatement, solution: ObservationSolution | undefined): string | null {
  const source = statement.source;
  if (source.kind === 'ephemeris') return 'ERFA ephemeris';
  if (source.kind === 'catalog') return source.release;
  if (source.kind !== 'solution_field') return null;
  const row = sourceRow(source, statement.quantity);
  if (typeof row === 'string') return null;
  if (row.rule === 'solve') return "this photo's solve";
  if (row.rule === 'pose') return null;
  // A finding's own field: the statement must name the finding the path reaches
  const [root, index] = source.path;
  if (root !== 'findings' || typeof index !== 'number') return null;
  const finding = solution?.findings[index];
  if (!finding || statement.subject.kind !== 'finding' || finding.id !== statement.subject.findingId) return null;
  if (row.rule === 'measured') return "this photo's pixels";
  if (row.rule === 'own_class') return finding.evidenceClass === 'predicted' ? 'ERFA ephemeris' : "this photo's solve";
  // A catalog row by its catalog's name, with "parallax" for a distance its parallax measured
  const release = finding.catalogRef?.releaseVersion;
  if (!release) return null;
  const parallax = source.path[2] === 'catalogRef' && source.path[3] === 'bailerJonesDistancePc' && finding.catalogRef?.bailerJonesDistancePc?.method === 'geometric_parallax';
  return parallax ? `${release} parallax` : release;
}

/**
 * The small line under an answer, beside a statement's evidence tag, in the approved form "value unit · source"
 * (ask, approved 2026-11-12: "7.68 parsecs · Hipparcos 2007 parallax"; every other kind as C7 decides). None for a
 * text value, or where its source has no words; nothing is written that the statement and the row it names do not
 * carry.
 */
export function statementLine(statement: AnswerStatement, solution: ObservationSolution | undefined): string | null {
  if (statement.value.kind !== 'number') return null;
  const source = sourceWords(statement, solution);
  if (source === null) return null;
  return `${valueWords(statement.value.amount, statement.value.unit)} · ${source}`;
}
