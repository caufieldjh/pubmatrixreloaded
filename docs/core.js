// Logic for the page, kept free of the DOM so it can be tested with node --test.
// Several values mirror the Python tool; tests/test_web.py checks they agree.

export const RELATION_TYPES = [
  'ANY', 'associate', 'cause', 'compare', 'cotreat', 'drug_interact', 'inhibit',
  'interact', 'negative_correlate', 'positive_correlate', 'prevent', 'stimulate', 'treat',
];
export const CONCEPTS = ['chemical', 'disease', 'gene', 'variant'];
export const TYPICAL_REQUESTS_PER_SECOND = 2;
export const LARGE_RUN_REQUESTS = 1000;
export const HEAT_STEPS = 6;
export const SYNONYM_SEPARATOR = ' | ';
export const WEB_SEARCH = 'https://www.ncbi.nlm.nih.gov/research/pubtator3/docsum?text=';
export const INSTALL = 'uvx --from git+https://github.com/caufieldjh/pubmatrixreloaded pubmatrix';
export const EXAMPLES = [
  {
    id: 'cancer-drugs',
    title: 'Which drugs treat which cancers',
    question: '12 cancer drugs by 6 cancers, counting extracted "treat" relations.',
    reading: 'Most drugs peak at their established use: imatinib with chronic myeloid leukemia, '
      + 'vemurafenib with melanoma, 5-fluorouracil with colorectal cancer. Aspirin with colorectal '
      + 'cancer and metformin with breast cancer are studied uses, not approved ones: a "treat" '
      + 'relation means a paper says so, and is extracted by machine.',
  },
  {
    id: 'drug-metabolism',
    title: 'Which enzymes act on which drugs',
    question: '6 cytochrome P450 enzymes by 10 drugs, counting any extracted relation.',
    reading: 'Each enzyme picks out its textbook drugs with nothing but the literature to go on: '
      + 'CYP2C9 with warfarin, CYP2C19 with clopidogrel, CYP2D6 with tamoxifen and codeine, CYP1A2 with caffeine, '
      + 'CYP2B6 with efavirenz, CYP3A4 with midazolam and ketoconazole.',
  },
  {
    id: 'disease-genes',
    title: 'Which genes go with which diseases',
    question: '16 genes by 5 neurodegenerative diseases, counting documents that mention both.',
    reading: 'SNCA and LRRK2 sit with Parkinson disease and HTT with Huntington disease. Raw counts '
      + 'favor large fields, though: SOD1 is a classic ALS gene yet shows more documents for '
      + 'Alzheimer disease, whose literature is about six times larger. Compare with the '
      + '"docs" figure under each heading.',
  },
  {
    id: 'chemical-screen',
    title: 'Which chemicals were tested in which assays',
    question: '20 environmental chemicals by 6 toxicity assays, counting documents that mention both.',
    reading: 'Bisphenol A stands out in the uterotrophic assay, a test of estrogen-like activity. '
      + 'Be wary of formaldehyde: many of its comet assay documents are about something else, '
      + 'and it is likely there as a lab reagent. A mention in full text is not a test.',
  },
];
export const TERMS_FILE = 'terms.txt';
export const MODIFIERS_FILE = 'modifiers.txt';

/** One term per line, without blanks, # comments, or repeats. */
export function parseTerms(text) {
  const lines = text.split(/\r?\n/).map((line) => line.trim());
  return [...new Set(lines.filter((line) => line && !line.startsWith('#')))];
}

function hasStrayQuote(part) {
  const term = part.trim();
  if (term.startsWith('@')) return false;
  const quoted = term.length > 1 && term.startsWith('"') && term.endsWith('"');
  return (quoted ? term.slice(1, -1) : term).includes('"');
}

/** Terms the tool would refuse, as it does not guess at stray quote marks. */
export function termsWithStrayQuotes(terms) {
  return terms.filter((term) => term.split(SYNONYM_SEPARATOR).some(hasStrayQuote));
}

/** Terms that cannot work in a relation search, which needs one entity per side. */
export function termsUnfitForRelations(terms) {
  return terms.filter((term) => term.includes(SYNONYM_SEPARATOR) || term.startsWith('"'));
}

export function estimate(rows, cols) {
  const cells = rows * cols;
  const requests = cells + 2 * (rows + cols);
  return { cells, requests, seconds: requests / TYPICAL_REQUESTS_PER_SECOND };
}

export function formatDuration(seconds) {
  if (seconds < 90) return `${Math.round(seconds)} seconds`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} minutes`;
  return `${(seconds / 3600).toFixed(1)} hours`;
}

export function shellQuote(value) {
  if (/^[A-Za-z0-9_\/.:=@%+-]+$/.test(value)) return value;
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

export function buildCommand({ output, relation, termsType, modifiersType, quietHours }) {
  const parts = [INSTALL, 'run', '-T', TERMS_FILE, '-M', MODIFIERS_FILE];
  if (termsType) parts.push('--terms-type', termsType);
  if (modifiersType) parts.push('--modifiers-type', modifiersType);
  if (relation) parts.push('--relation', relation);
  if (quietHours) parts.push('--quiet-hours');
  parts.push('-o', shellQuote(output || 'pubmatrix'));
  return parts.join(' ');
}

/** Same scale as pubmatrix.matrix.heat_step. */
export function heatStep(count, low, high) {
  if (high <= low) return Math.floor(HEAT_STEPS / 2);
  const fraction = Math.log(count / low) / Math.log(high / low);
  return 1 + Math.min(HEAT_STEPS - 1, Math.floor(HEAT_STEPS * fraction));
}

/** Links come from the query, never from the file, which may not be trustworthy. */
export function cellLink(query) {
  return WEB_SEARCH + encodeURIComponent(query);
}

/** Why this is not a usable results file, or null if it is. */
export function resultProblem(data) {
  const isList = (value) => Array.isArray(value);
  if (!data || typeof data !== 'object' || !isList(data.rows) || !isList(data.columns) || !isList(data.cells)) {
    return 'This does not look like a PubMatrix Reloaded .json results file.';
  }
  if (data.rows.length === 0 || data.columns.length === 0) return 'This results file has no terms.';
  if (data.cells.length !== data.rows.length * data.columns.length) {
    return 'This results file is damaged: its cell count does not match its terms.';
  }
  const fine = (cell) => cell && typeof cell.query === 'string'
    && (cell.count === null || (Number.isInteger(cell.count) && cell.count >= 0));
  if (!data.cells.every(fine)) return 'This results file is damaged: a cell has no valid count or query.';
  return null;
}

/** Cells are stored row by row; labels come from the cells themselves. */
export function toGrid(data) {
  const width = data.columns.length;
  const counts = data.cells.map((cell) => cell.count).filter((count) => count > 0);
  return {
    rowLabels: data.rows.map((_, i) => String(data.cells[i * width].row ?? data.rows[i].input)),
    colLabels: data.columns.map((_, j) => String(data.cells[j].column ?? data.columns[j].input)),
    cell: (i, j) => data.cells[i * width + j],
    low: counts.length ? counts.reduce((a, b) => Math.min(a, b)) : 0,
    high: counts.length ? counts.reduce((a, b) => Math.max(a, b)) : 0,
  };
}
