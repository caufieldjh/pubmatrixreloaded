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

// Searching in the browser. Results name the data release and dataset revision.
export const DATASET = 'caufieldjh/pubmatrix-pubtator3';
export const DATASET_REVISION = '12df2b69e5546e787e5e4abc2ee373deaa00a186';
export const DATA_RELEASE = '2026-10-06';
export const DUCKDB_VERSION = '1.33.1-dev57.0';
export const SEARCH_WORKERS = 4;
export const BROWSER_TYPES = ['chemical', 'disease', 'gene'];

// Measured against Hugging Face on one home connection, rounded up. Real runs vary.
export const STARTUP_SECONDS = 10;
export const SECONDS_PER_LOOKUP = 0.05;
export const SECONDS_PER_TERM = 0.1;
export const RELATIONS_SECONDS = 15;

// Phrases come from Europe PMC, one list of documents each. Its pages often fail with
// HTTP 503, so lists arrive at about 65 documents a second with four requests running.
export const PHRASE_API = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search';
export const PHRASE_PAGE_SIZE = 200;
export const PHRASE_REQUESTS = 4;
export const PHRASE_DOCS_PER_SECOND = 50;
export const PHRASE_MAX_DOCS = 100000;
export const PHRASE_SLICE_ABOVE = 2000;
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

/** Like formatDuration, for promises to a person: short waits are not worth counting. */
export function formatWait(seconds) {
  return seconds < 10 ? 'a few seconds' : `about ${formatDuration(seconds)}`;
}

export function shellQuote(value) {
  if (/^[A-Za-z0-9_\/.:=@%+-]+$/.test(value)) return value;
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

export function buildCommand({ output, relation, termsType, modifiersType, quietHours, colors }) {
  const parts = [INSTALL, 'run', '-T', TERMS_FILE, '-M', MODIFIERS_FILE];
  // The command line tool reads anything without an exact entity match as a phrase.
  if (CONCEPTS.includes(termsType)) parts.push('--terms-type', termsType);
  if (CONCEPTS.includes(modifiersType)) parts.push('--modifiers-type', modifiersType);
  if (relation) parts.push('--relation', relation);
  if (quietHours) parts.push('--quiet-hours');
  if (colors) parts.push('--colors', colors);
  parts.push('-o', shellQuote(output || 'pubmatrix'));
  return parts.join(' ');
}

/** Lowercases ASCII only, as the names table was built. */
export function normalizeName(text) {
  return text.trim().replace(/[A-Z]/g, (letter) => letter.toLowerCase());
}

const GREEK = {
  α: 'alpha', β: 'beta', γ: 'gamma', δ: 'delta', ε: 'epsilon', κ: 'kappa', λ: 'lambda', μ: 'mu', µ: 'mu', ω: 'omega',
};

/** The literature mostly spells Greek letters out: "17beta-estradiol", not "17β-estradiol". */
export function spellGreek(text) {
  return text.replace(/[αβγδεκλμµω]/g, (letter) => GREEK[letter]);
}

/** The shard holding a value, given the first value of each sorted shard. */
export function shardIndex(starts, value) {
  let low = 0;
  let high = starts.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (starts[middle] <= value) low = middle;
    else high = middle - 1;
  }
  return low;
}

/** How to look up one part of a term, or why it cannot be searched here. */
export function classifyPart(part, type) {
  const text = part.trim();
  if (type === 'phrase') {
    if (text.slice(1, -1).includes('"')) return { problem: 'Stray quote mark. Write alternatives as a | b.' };
    return { phrase: text.replace(/^"(.*)"$/, '$1') };
  }
  if (type === 'variant') {
    return { problem: 'Variants can only be searched with the command line tool.' };
  }
  if (text.startsWith('@')) {
    return { problem: 'IDs like this work in the command line tool. Here, type a name.' };
  }
  const quoted = text.length > 1 && text.startsWith('"') && text.endsWith('"');
  if ((quoted ? text.slice(1, -1) : text).includes('"')) {
    return { problem: 'Stray quote mark. Write alternatives as a | b.' };
  }
  if (quoted) return { phrase: text.slice(1, -1) };
  if (/^MESH:[CD]\d+$/.test(text)) {
    if (type === 'disease' || type === 'chemical') return { id: text, type };
    return { problem: 'To use a MESH ID, set "Look up as" to chemical or disease.' };
  }
  if (type === 'gene' && /^\d+$/.test(text)) return { id: text, type };
  return { name: normalizeName(text), type };
}

/** Europe PMC search for documents with a PubMed ID, optionally in a span of years. */
export function phraseQuery(phrase, years) {
  const words = spellGreek(phrase).replaceAll('"', '');
  const span = years ? ` AND (FIRST_PDATE:[${years[0]}-01-01 TO ${years[1]}-12-31])` : '';
  return `"${words}" AND SRC:MED${span}`;
}

/**
 * Spans of years to fetch side by side. One list reads page after page, so a long list
 * is split up. Most of the literature is recent, so recent years get a span each.
 */
export function yearSlices(hits, thisYear) {
  if (hits <= PHRASE_SLICE_ABOVE) return [null];
  const slices = [[1800, 2005]];
  for (let year = 2006; year <= thisYear; year += 1) slices.push([year, year]);
  return slices;
}

export function splitParts(term) {
  return term.split(SYNONYM_SEPARATOR).map((part) => part.trim()).filter(Boolean);
}

/** Merge sorted lists of document IDs, dropping repeats. */
export function sortedUnion(lists) {
  if (lists.length === 1) return lists[0];
  const all = new Int32Array(lists.reduce((total, list) => total + list.length, 0));
  let offset = 0;
  for (const list of lists) {
    all.set(list, offset);
    offset += list.length;
  }
  all.sort();
  let kept = 0;
  for (let i = 0; i < all.length; i += 1) {
    if (i === 0 || all[i] !== all[i - 1]) {
      all[kept] = all[i];
      kept += 1;
    }
  }
  return all.subarray(0, kept);
}

/** Count the IDs two sorted lists share. */
export function intersectionSize(a, b) {
  let i = 0;
  let j = 0;
  let shared = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      shared += 1;
      i += 1;
      j += 1;
    } else if (a[i] < b[j]) i += 1;
    else j += 1;
  }
  return shared;
}

/** Key of an entity in relations.parquet. */
export function relationKey({ type, concept }) {
  return `${type[0].toUpperCase()}${type.slice(1)}|${concept}`;
}

/** Search words for a term, used to link a cell to PubTator3. */
export function linkQuery(term) {
  const parts = splitParts(term).map((part) => `"${part.replaceAll('"', '')}"`);
  return parts.length > 1 ? `(${parts.join(' OR ')})` : parts[0];
}

/** Expected seconds to check terms, and then to search. */
export function browserEstimate(rows, cols, relation, phraseDocuments = 0) {
  const terms = rows + cols;
  const check = STARTUP_SECONDS + terms * SECONDS_PER_LOOKUP;
  const search = relation ? RELATIONS_SECONDS : terms * SECONDS_PER_TERM + phraseDocuments / PHRASE_DOCS_PER_SECOND;
  return { check, search };
}

/** Seconds left, from the pace so far. */
export function remainingSeconds(done, total, elapsedSeconds) {
  if (done === 0) return null;
  return (elapsedSeconds / done) * (total - done);
}

function csvField(value) {
  const text = value === null || value === undefined ? '' : String(value);
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/** The same plain grid the command line tool writes. Blank means unknown. */
export function toCsv(data) {
  const grid = toGrid(data);
  const lines = [['term', ...grid.colLabels].map(csvField).join(',')];
  grid.rowLabels.forEach((label, i) => {
    const counts = grid.colLabels.map((_, j) => grid.cell(i, j).count);
    lines.push([label, ...counts].map(csvField).join(','));
  });
  return `${lines.join('\n')}\n`;
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
  // Reduce rather than spread: a large matrix has more cells than a call has arguments.
  return {
    rowLabels: data.rows.map((_, i) => String(data.cells[i * width].row ?? data.rows[i].input)),
    colLabels: data.columns.map((_, j) => String(data.cells[j].column ?? data.columns[j].input)),
    cell: (i, j) => data.cells[i * width + j],
    low: counts.length ? counts.reduce((a, b) => Math.min(a, b)) : 0,
    high: counts.length ? counts.reduce((a, b) => Math.max(a, b)) : 0,
  };
}

/**
 * Color schemes for the matrix: three colors, for the fewest documents, the middle, and the most.
 * 'auto' keeps the page's own blue, which follows the light or dark theme.
 */
export const COLOR_PRESETS = {
  auto: { label: 'Blue, following light or dark mode (default)' },
  blue: { label: 'Blue', colors: ['#cde2fb', '#4a89d6', '#0d366b'] },
  red: { label: 'Red', colors: ['#fde0dc', '#dc4b35', '#6e0d0d'] },
  green: { label: 'Green', colors: ['#dcf2dc', '#3f9b4f', '#0d4a1c'] },
  black: { label: 'Black (light gray to black)', colors: ['#eeeeee', '#888888', '#111111'] },
  white: { label: 'White (dark gray to white)', colors: ['#222222', '#888888', '#ffffff'] },
};

/** The --colors value for a choice from the page, or null for the default. */
export function colorsArgument(choice) {
  if (choice.preset === 'custom') return choice.colors.map((hex) => hex.slice(1)).join(',');
  return COLOR_PRESETS[choice.preset]?.colors ? choice.preset : null;
}

/** '#abc', 'abc', '#aabbcc' or 'aabbcc' as '#aabbcc', or null if it is not a hex color. */
export function parseHex(text) {
  const match = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(text).trim());
  if (!match) return null;
  const digits = match[1].length === 3 ? [...match[1]].map((d) => d + d).join('') : match[1];
  return `#${digits.toLowerCase()}`;
}

const channels = (hex) => [1, 3, 5].map((at) => parseInt(hex.slice(at, at + 2), 16) / 255);
const toLinear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const fromLinear = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);

function toOklab(hex) {
  const [r, g, b] = channels(hex).map(toLinear);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

function fromOklab([L, A, B]) {
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3;
  const rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  const byte = (c) => Math.round(255 * Math.min(1, Math.max(0, fromLinear(c))));
  return `#${rgb.map((c) => byte(c).toString(16).padStart(2, '0')).join('')}`;
}

/** Relative luminance, as WCAG defines it. */
export function luminance(hex) {
  const [r, g, b] = channels(hex).map(toLinear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Black or white, whichever reads better on this background. */
export function inkFor(hex) {
  const light = luminance(hex);
  return (1.05) / (light + 0.05) > (light + 0.05) / 0.05 ? '#ffffff' : '#000000';
}

/**
 * HEAT_STEPS colors from low through middle to high, blended in OKLab so the steps look even.
 * Each step comes with the ink color that reads best on it.
 */
export function heatScale([low, middle, high]) {
  const ends = [low, middle, high].map(toOklab);
  return Array.from({ length: HEAT_STEPS }, (_, n) => {
    const t = n / (HEAT_STEPS - 1);
    const [from, to, f] = t <= 0.5 ? [ends[0], ends[1], t * 2] : [ends[1], ends[2], t * 2 - 1];
    const background = fromOklab(from.map((value, k) => value + (to[k] - value) * f));
    return { background, ink: inkFor(background) };
  });
}
