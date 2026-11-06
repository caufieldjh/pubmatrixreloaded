import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  buildCommand, cellLink, estimate, formatDuration, heatStep, parseTerms, resultProblem,
  shellQuote, termsUnfitForRelations, termsWithStrayQuotes, toGrid,
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
