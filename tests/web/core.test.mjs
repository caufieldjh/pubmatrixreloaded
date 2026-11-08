import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  PHRASE_DOCS_PER_SECOND, browserEstimate, buildCommand, cellLink, classifyPart, estimate,
  formatDuration, heatStep, intersectionSize, linkQuery, normalizeName, parseTerms, phraseQuery,
  relationKey, remainingSeconds,
  resultProblem, shardIndex, shellQuote, sortedUnion, spellGreek, splitParts, termsUnfitForRelations,
  termsWithStrayQuotes, toCsv, toGrid, yearSlices,
} from '../../docs/core.js';

test('parseTerms drops blanks, comments, and repeats', () => {
  assert.deepEqual(parseTerms('# note\r\n\r\nBRCA1\n  TP53  \nBRCA1\n'), ['BRCA1', 'TP53']);
});

test('stray quotes are found the way the tool finds them', () => {
  const fine = ['"DNA repair"', 'a | "b"', '@GENE_"x', 'plain'];
  assert.deepEqual(termsWithStrayQuotes(fine), []);
  const bad = ['"Ames test" OR "Ames assay"', 'a | b"c'];
  assert.deepEqual(termsWithStrayQuotes(bad), bad);
});

test('synonyms and phrases cannot be used in relation searches', () => {
  assert.deepEqual(termsUnfitForRelations(['@GENE_A', 'a | b', '"c"', 'd']), ['a | b', '"c"']);
});

test('estimate matches the command line tool', () => {
  assert.deepEqual(estimate(8, 4), { cells: 32, requests: 56, seconds: 28 });
  assert.equal(formatDuration(28), '28 seconds');
  assert.equal(formatDuration(600), '10 minutes');
  assert.equal(formatDuration(5008), '83 minutes');
  assert.equal(formatDuration(20400), '5.7 hours');
});

test('shellQuote leaves safe text and protects the rest', () => {
  assert.equal(shellQuote('output/demo-1'), 'output/demo-1');
  assert.equal(shellQuote('my results'), "'my results'");
  assert.equal(shellQuote("it's; rm x"), `'it'\\''s; rm x'`);
  assert.equal(shellQuote('$(boom)'), "'$(boom)'");
});

test('buildCommand includes only what was chosen', () => {
  const plain = buildCommand({ output: '', relation: '', termsType: '', modifiersType: '', quietHours: false });
  assert.match(plain, /pubmatrix run -T terms.txt -M modifiers.txt -o pubmatrix$/);
  const full = buildCommand({
    output: 'my run', relation: 'treat', termsType: 'chemical', modifiersType: 'disease', quietHours: true,
  });
  assert.match(full, /--terms-type chemical --modifiers-type disease --relation treat --quiet-hours -o 'my run'$/);
});

test('heatStep agrees with the Python scale', () => {
  assert.equal(heatStep(1878, 1878, 92807), 1);
  assert.equal(heatStep(92807, 1878, 92807), 6);
  assert.equal(heatStep(13216, 1878, 92807), 4);
  assert.equal(heatStep(5, 5, 5), 3);
});

test('cellLink builds a PubTator3 link from the query alone', () => {
  assert.equal(
    cellLink('@GENE_A AND "b c"'),
    'https://www.ncbi.nlm.nih.gov/research/pubtator3/docsum?text=%40GENE_A%20AND%20%22b%20c%22',
  );
});

const good = () => ({
  rows: [{ input: '@GENE_A', total: 5 }, { input: 'b', total: 5 }],
  columns: [{ input: 'c', total: 5 }],
  cells: [
    { row: 'A', column: 'c', count: 3, query: 'q1' },
    { row: 'b', column: 'c', count: null, query: 'q2', error: 'why' },
  ],
});

test('resultProblem accepts a results file', () => {
  assert.equal(resultProblem(good()), null);
});

test('resultProblem refuses other files', () => {
  for (const data of [null, 7, [], {}, { rows: [], columns: [], cells: [] }]) {
    assert.ok(resultProblem(data));
  }
  const short = good();
  short.cells.pop();
  assert.match(resultProblem(short), /damaged/);
  const negative = good();
  negative.cells[0].count = -1;
  assert.match(resultProblem(negative), /damaged/);
  const wrongType = good();
  wrongType.cells[0].count = '<b>3</b>';
  assert.match(resultProblem(wrongType), /damaged/);
});

test('toGrid reads cells row by row and ignores blanks and zeros for the range', () => {
  const data = good();
  data.rows.push({ input: 'z', total: 5 });
  data.cells.push({ row: 'z', column: 'c', count: 0, query: 'q3' });
  const grid = toGrid(data);
  assert.deepEqual(grid.rowLabels, ['A', 'b', 'z']);
  assert.deepEqual(grid.colLabels, ['c']);
  assert.equal(grid.cell(1, 0).error, 'why');
  assert.deepEqual([grid.low, grid.high], [3, 3]);
});

test('normalizeName lowers ASCII only, as the names table was built', () => {
  assert.equal(normalizeName('  BRCA1 '), 'brca1');
  assert.equal(normalizeName('17Β-Estradiol'), '17Β-estradiol');
});

test('shardIndex finds the last shard starting at or before a value', () => {
  const starts = ['a', 'f', 'm'];
  assert.equal(shardIndex(starts, 'a'), 0);
  assert.equal(shardIndex(starts, 'e'), 0);
  assert.equal(shardIndex(starts, 'f'), 1);
  assert.equal(shardIndex(starts, 'z'), 2);
  assert.equal(shardIndex(starts, '0'), 0);
});

test('classifyPart reads names and IDs', () => {
  assert.deepEqual(classifyPart('Tamoxifen', 'chemical'), { name: 'tamoxifen', type: 'chemical' });
  assert.deepEqual(classifyPart('672', 'gene'), { id: '672', type: 'gene' });
  assert.deepEqual(classifyPart('MESH:D001943', 'disease'), { id: 'MESH:D001943', type: 'disease' });
  assert.deepEqual(classifyPart('672', ''), { name: '672', type: '' });
});

test('classifyPart explains what cannot be searched here', () => {
  assert.deepEqual(classifyPart('"comet assay"', ''), { phrase: 'comet assay' });
  assert.match(classifyPart('a"b', '').problem, /Stray/);
  assert.match(classifyPart('@GENE_BRCA1', '').problem, /type a name/);
  assert.match(classifyPart('MESH:D001943', '').problem, /Look up as/);
  assert.match(classifyPart('V600E', 'variant').problem, /command line/);
});

test('splitParts reads alternatives', () => {
  assert.deepEqual(splitParts('a | b  |  c'), ['a', 'b', 'c']);
  assert.deepEqual(splitParts('a|b'), ['a|b']);
});

test('sortedUnion merges and removes repeats', () => {
  const merged = sortedUnion([Int32Array.of(1, 3, 5), Int32Array.of(3, 4)]);
  assert.deepEqual([...merged], [1, 3, 4, 5]);
  const one = Int32Array.of(2, 9);
  assert.equal(sortedUnion([one]), one);
});

test('intersectionSize counts shared documents', () => {
  assert.equal(intersectionSize(Int32Array.of(1, 3, 5, 7), Int32Array.of(3, 4, 7, 9)), 2);
  assert.equal(intersectionSize(Int32Array.of(), Int32Array.of(1)), 0);
});

test('relationKey matches the bulk file format', () => {
  assert.equal(relationKey({ type: 'gene', concept: '672' }), 'Gene|672');
  assert.equal(relationKey({ type: 'disease', concept: 'MESH:D1' }), 'Disease|MESH:D1');
});

test('linkQuery builds words for a PubTator3 search', () => {
  assert.equal(linkQuery('tamoxifen'), '"tamoxifen"');
  assert.equal(linkQuery('a | b'), '("a" OR "b")');
});

test('browserEstimate grows with terms, and relations read one file', () => {
  const small = browserEstimate(10, 5, '');
  const large = browserEstimate(1000, 8, '');
  assert.ok(large.search > small.search);
  assert.equal(browserEstimate(10, 5, 'treat').search, browserEstimate(1000, 8, 'treat').search);
});

test('remainingSeconds uses the pace so far', () => {
  assert.equal(remainingSeconds(0, 10, 5), null);
  assert.equal(remainingSeconds(5, 10, 20), 20);
  assert.equal(remainingSeconds(10, 10, 20), 0);
});

test('toCsv leaves unknown cells blank and quotes as needed', () => {
  const csv = toCsv({
    rows: [{ input: 'a' }],
    columns: [{ input: 'b' }, { input: 'c' }],
    cells: [
      { row: 'x, y', column: 'b', count: 3, query: 'q' },
      { row: 'x, y', column: 'c', count: null, query: 'q' },
    ],
  });
  assert.equal(csv, 'term,b,c\n"x, y",3,\n');
});

test('spellGreek spells letters out', () => {
  assert.equal(spellGreek('17β-estradiol'), '17beta-estradiol');
  assert.equal(spellGreek('tamoxifen'), 'tamoxifen');
});

test('phraseQuery keeps to documents with PubMed IDs', () => {
  assert.equal(phraseQuery('comet assay'), '"comet assay" AND SRC:MED');
  assert.equal(
    phraseQuery('17β-estradiol', [2010, 2010]),
    '"17beta-estradiol" AND SRC:MED AND (FIRST_PDATE:[2010-01-01 TO 2010-12-31])',
  );
});

test('yearSlices covers every year without overlap', () => {
  assert.deepEqual(yearSlices(500, 2026), [null]);
  const slices = yearSlices(30000, 2026);
  assert.deepEqual(slices[0], [1800, 2005]);
  assert.deepEqual(slices[1], [2006, 2006]);
  assert.deepEqual(slices.at(-1), [2026, 2026]);
  slices.slice(1).forEach(([from, to], i) => {
    assert.equal(from, to);
    assert.equal(from, slices[i][1] + 1);
  });
});

test('browserEstimate counts phrase documents', () => {
  const entities = browserEstimate(10, 5, '').search;
  assert.equal(browserEstimate(10, 5, '', PHRASE_DOCS_PER_SECOND * 60).search, entities + 60);
  assert.equal(browserEstimate(10, 5, 'treat', 1000000).search, browserEstimate(10, 5, 'treat').search);
});

test('phrases only reads everything as wording', () => {
  assert.deepEqual(classifyPart('LLNA', 'phrase'), { phrase: 'LLNA' });
  assert.deepEqual(classifyPart('"comet assay"', 'phrase'), { phrase: 'comet assay' });
});

test('buildCommand leaves out the browser-only phrase type', () => {
  const command = buildCommand({ output: '', relation: '', termsType: 'chemical', modifiersType: 'phrase', quietHours: false });
  assert.match(command, /--terms-type chemical -o/);
  assert.doesNotMatch(command, /phrase/);
});
