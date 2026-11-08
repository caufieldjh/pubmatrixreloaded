import {
  COLOR_PRESETS, CONCEPTS, DATASET, DATASET_REVISION, DATA_RELEASE, EXAMPLES, HEAT_STEPS, LARGE_RUN_REQUESTS,
  MODIFIERS_FILE, PHRASE_DOCS_PER_SECOND, RELATION_TYPES, SECONDS_PER_TERM, TERMS_FILE, browserEstimate, buildCommand, cellLink, estimate,
  formatDuration, formatWait, heatScale, heatStep, parseHex, intersectionSize, linkQuery, parseTerms, relationKey,
  remainingSeconds, resultProblem, splitParts, termsUnfitForRelations, termsWithStrayQuotes, toCsv, toGrid,
} from './core.js';

const $ = (id) => document.getElementById(id);
const number = (value) => value.toLocaleString('en-US');

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function addOptions(select, entries) {
  for (const [value, label] of entries) {
    const option = element('option', label);
    option.value = value;
    select.append(option);
  }
}

// Planning

function currentPlan() {
  return {
    terms: parseTerms($('terms').value),
    modifiers: parseTerms($('modifiers').value),
    options: {
      output: $('output').value.trim(),
      relation: $('relation').value,
      termsType: $('terms-type').value,
      modifiersType: $('modifiers-type').value,
      quietHours: $('quiet').checked,
    },
  };
}

function planWarnings({ terms, modifiers, options }, requests) {
  const warnings = [];
  const everything = [...terms, ...modifiers];
  const stray = termsWithStrayQuotes(everything);
  if (stray.length) {
    warnings.push(`Stray quote marks will be refused: ${stray.join(', ')}. Write alternatives as a | b.`);
  }
  if (options.relation) {
    const unfit = termsUnfitForRelations(everything);
    if (unfit.length) {
      warnings.push(`These will come back blank, since relation counts need one entity per side: ${unfit.join(', ')}.`);
    }
    warnings.push('Any term without an exact entity match will also come back blank. @ IDs are safest here.');
  }
  if (requests > LARGE_RUN_REQUESTS && !options.quietHours) {
    warnings.push('This is a large run. Consider waiting for quiet hours to keep it off NCBI\'s busy times.');
  }
  if (options.quietHours) {
    warnings.push('The computer must stay on and awake until this finishes, and the command waits while hours are not quiet.');
  }
  return warnings;
}

function updatePlan() {
  const plan = currentPlan();
  const ready = plan.terms.length > 0 && plan.modifiers.length > 0;
  const { cells, requests, seconds } = estimate(plan.terms.length, plan.modifiers.length);
  $('estimate').textContent = ready
    ? `${number(plan.terms.length)} × ${number(plan.modifiers.length)} = ${number(cells)} cells, up to `
      + `${number(requests)} requests, about ${formatDuration(seconds)} of sending.`
    : 'Enter at least one term and one modifier.';
  $('warnings').replaceChildren(...(ready ? planWarnings(plan, requests) : []).map((text) => element('li', text)));
  $('command').textContent = buildCommand(plan.options);
  updateTimeEstimate(plan, ready);
  resetCheck();
  $('save-terms').disabled = plan.terms.length === 0;
  $('save-modifiers').disabled = plan.modifiers.length === 0;
}

function download(name, text, type = 'text/plain') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = element('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

async function copyCommand() {
  const button = $('copy');
  try {
    await navigator.clipboard.writeText($('command').textContent);
    button.textContent = 'Copied';
  } catch {
    getSelection().selectAllChildren($('command'));
    button.textContent = 'Press Ctrl+C';
  }
  setTimeout(() => { button.textContent = 'Copy'; }, 2000);
}

// Searching in this browser.

let engine = null;
let checked = null;
let shown = null;
let running = false;

function updateTimeEstimate(plan, ready) {
  if (!ready) {
    $('time-estimate').textContent = 'Enter your terms for an estimate. Expect a few seconds to start, then about a '
      + 'tenth of a second per term, so 1000 terms take a few minutes.';
    return;
  }
  const { check, search } = browserEstimate(plan.terms.length, plan.modifiers.length, plan.options.relation);
  $('time-estimate').textContent = `Expect ${formatWait(check)} to check your terms, then ${formatWait(search)} `
    + 'to search, plus about a minute for every 3,000 documents of phrases. Checking shows how many documents '
    + 'each phrase has and gives a full estimate. All of this depends on your connection and on how busy Europe PMC '
    + 'is, and the page shows the time left as it goes.';
}

function resetCheck() {
  if (running) return;
  checked = null;
  $('checked').hidden = true;
  $('check-status').textContent = '';
  $('search-error').hidden = true;
}

function showSearchError(message) {
  $('search-error').textContent = message;
  $('search-error').hidden = false;
}

function setBusy(busy) {
  $('check').disabled = busy;
  for (const id of ['terms', 'modifiers', 'terms-type', 'modifiers-type', 'relation']) $(id).disabled = busy;
}

function describeTerm(term) {
  if (term.problem) return { read: term.unchecked ? 'Could not check' : 'Not found', note: term.problem };
  const read = term.parts.map((part) => (part.phrase === undefined ? `${part.type} ${part.concept}` : 'phrase')).join(' or ');
  const others = term.parts.flatMap((part) => part.alternatives.map(
    (other) => `${other.type} ${other.concept} (${number(other.mentions)})`,
  ));
  const notes = term.parts.filter((part) => part.phrase !== undefined).map((part) => (part.hits === 0
    ? `"${part.phrase}": no documents at Europe PMC; check the spelling`
    : `"${part.phrase}": ${number(part.hits)} documents at Europe PMC`));
  if (others.length) notes.push(`Can also mean: ${others.slice(0, 3).join(', ')}`);
  if (termKind(term) === 'entity and phrase') {
    notes.unshift('Mixes an entity with a phrase. If all of it is wording, set "Look up as" to phrases only');
  }
  return { read, note: notes.join('. ') };
}

function conceptLink(part) {
  if (part.phrase !== undefined) return 'phrase';
  const link = element('a', `${part.type} ${part.concept}`);
  link.href = part.type === 'gene'
    ? `https://www.ncbi.nlm.nih.gov/gene/${part.concept}`
    : `https://meshb.nlm.nih.gov/record/ui?ui=${part.concept.replace('MESH:', '')}`;
  link.target = '_blank';
  link.rel = 'noopener';
  return link;
}

function renderCheck(terms) {
  const head = element('tr');
  for (const title of ['Term', 'Read as', 'Note']) head.append(element('th', title));
  const rows = terms.map((term) => {
    const { read: label, note } = describeTerm(term);
    const row = element('tr');
    const read = element('td', term.problem ? label : null, term.problem ? 'notfound' : null);
    term.parts.forEach((part, i) => {
      if (i) read.append(' or ');
      read.append(conceptLink(part));
    });
    row.append(element('td', term.raw), read, element('td', note));
    return row;
  });
  rows.sort((a, b) => b.children[1].classList.contains('notfound') - a.children[1].classList.contains('notfound'));
  $('check-table').replaceChildren(head, ...rows);
}

async function checkTerms() {
  const plan = currentPlan();
  if (plan.terms.length === 0 || plan.modifiers.length === 0) {
    showSearchError('Enter at least one term and one modifier.');
    return;
  }
  resetCheck();
  setBusy(true);
  try {
    if (!engine) {
      $('check-status').textContent = 'Loading the search engine, about 37 MB, once per visit.';
      const { Engine } = await import('./search.js');
      engine = await Engine.start();
    }
    engine.stopped = false;
    const items = [
      ...plan.terms.map((raw) => ({ raw, type: plan.options.termsType })),
      ...plan.modifiers.map((raw) => ({ raw, type: plan.options.modifiersType })),
    ];
    $('check-status').textContent = 'Checking terms.';
    const fixed = await engine.resolveAll(items, (done, total) => {
      $('check-status').textContent = `Checking terms: part ${done} of ${total}.`;
    });
    const rows = fixed.slice(0, plan.terms.length);
    const cols = fixed.slice(plan.terms.length);
    checked = { rows, cols, relation: plan.options.relation };
    finishCheck(fixed, rows, cols, plan);
  } catch (error) {
    showSearchError(`The search engine could not start: ${error.message ?? error}. Check your connection and try again.`);
    engine = null;
    $('check-status').textContent = '';
  } finally {
    setBusy(false);
  }
}

function finishCheck(all, rows, cols, plan) {
  renderCheck(all);
  const missing = all.filter((term) => term.problem).length;
  const unchecked = all.filter((term) => term.unchecked).length;
  $('check-status').textContent = '';
  $('check-summary').textContent = missing
    ? `${number(all.length - missing)} of ${number(all.length)} terms found. Terms not found will come back blank, `
      + 'not zero. Check how each one reads below.'
    : `All ${number(all.length)} terms found. Check that each one reads as you meant.`;
  if (unchecked) {
    $('check-summary').textContent += ` ${number(unchecked)} could not be checked because of a connection problem: `
      + 'select Check terms to try again.';
  }
  const usable = (list) => list.filter((term) => !term.problem).length;
  const phrases = phrasesIn(rows, cols);
  const documents = phrases.reduce((total, { hits }) => total + hits, 0);
  const { search } = browserEstimate(usable(rows), usable(cols), plan.options.relation, documents);
  const possible = usable(rows) > 0 && usable(cols) > 0;
  const slowly = documents > 0
    ? ` Most of that is fetching ${number(documents)} documents for ${number(phrases.length)} phrases from Europe PMC, `
      + 'which is often busy and may take longer.'
    : '';
  $('run-estimate').textContent = possible
    ? `This search should take ${formatWait(search)}.${slowly}`
    : 'Nothing can be searched yet: each list needs at least one term that was found.';
  $('start').disabled = !possible;
  $('checked').hidden = false;
}

function phrasesIn(rows, cols) {
  const seen = new Map();
  for (const term of [...rows, ...cols]) {
    for (const part of term.parts) {
      if (part.phrase !== undefined) seen.set(part.phrase, { phrase: part.phrase, hits: part.hits });
    }
  }
  return [...seen.values()];
}

function reportProgress(stage, done, total, startedAt) {
  const elapsed = (performance.now() - startedAt) / 1000;
  const left = remainingSeconds(done, total, elapsed);
  const percent = Math.min(100, Math.round((100 * done) / total));
  $('progress').value = Math.min(1, done / total);
  $('run-status').textContent = `${percent}% done. ${stage} `
    + (left === null ? 'Working out the time left.' : `Time left: ${formatWait(left)}.`);
  document.title = `${percent}% PubMatrix Reloaded`;
}

function termKind(term) {
  if (term.problem) return 'not found';
  const phrases = term.parts.filter((part) => part.phrase !== undefined).length;
  if (phrases === 0) return 'entity';
  return phrases === term.parts.length ? 'phrase' : 'entity and phrase';
}

function termRecord(term, type, total) {
  return {
    input: term.raw,
    kind: termKind(term),
    query: term.parts.map((part) => part.concept ?? `"${part.phrase}"`).join(' OR '),
    name: null,
    biotype: term.parts[0]?.type ?? null,
    suggestion: null,
    total: total ?? null,
    note: term.problem ?? describeTerm(term).note,
  };
}

function buildResult(search) {
  const { rows, cols, relation, totals, counts, errors, startedAt, stopped } = search;
  const label = (term) => splitParts(term.raw)[0];
  const cells = [];
  rows.forEach((row, i) => cols.forEach((col, j) => {
    const count = counts[i]?.[j] ?? null;
    const blocked = row.problem || col.problem;
    let error = null;
    if (count === null) {
      error = blocked ? 'A term in this pair was not found.' : errors[i]?.[j] ?? 'The search stopped before reaching this cell.';
    }
    cells.push({
      row: label(row),
      column: label(col),
      count,
      query: `${linkQuery(row.raw)} AND ${linkQuery(col.raw)}`,
      retrieved: null,
      error,
      note: null,
    });
  }));
  const blanks = cells.filter((cell) => cell.count === null).length;
  return {
    run: {
      tool: 'PubMatrix Reloaded web page',
      command: null,
      source: `PubTator3 bulk annotations, release ${DATA_RELEASE}, from ${DATASET} at ${DATASET_REVISION}`
        + (phrasesIn(rows, cols).length ? `; phrases from Europe PMC, fetched ${startedAt.slice(0, 10)}` : ''),
      mode: relation ? 'relation' : 'co-occurrence',
      relation: relation || null,
      started: startedAt,
      finished: new Date().toISOString(),
      status: stopped ? 'partial: stopped' : 'complete',
      cells: cells.length,
      cells_without_value: blanks,
    },
    rows: rows.map((term, i) => termRecord(term, 'row', totals.rows[i])),
    columns: cols.map((term, j) => termRecord(term, 'column', totals.cols[j])),
    cells,
  };
}

async function searchOverlaps(search, tick) {
  const { rows, cols, totals, counts, errors } = search;
  // Work is measured in expected seconds, so the bar and the time left stay honest
  // when slow phrases and quick entities are mixed.
  const phrases = phrasesIn(rows, cols);
  const phraseSeconds = phrases.reduce((total, { hits }) => total + hits, 0) / PHRASE_DOCS_PER_SECOND;
  const termCount = rows.concat(cols).filter((term) => !term.problem).length;
  const workTotal = phraseSeconds + termCount * SECONDS_PER_TERM;
  let phraseDone = 0;
  const phraseFailures = await engine.fetchPhrases(phrases, (documents) => {
    phraseDone += documents / PHRASE_DOCS_PER_SECOND;
    tick('Fetching phrases from Europe PMC.', Math.min(phraseDone, phraseSeconds), workTotal);
  });
  if (engine.stopped) return;
  search.phraseFailures = phraseFailures;
  const usableCols = cols.map((col, j) => ({ col, j })).filter((item) => !item.col.problem);
  const usableRows = rows.map((row, i) => ({ row, i })).filter((item) => !item.row.problem);
  let done = 0;
  const step = () => {
    done += 1;
    tick('Reading entities.', phraseSeconds + done * SECONDS_PER_TERM, workTotal);
  };
  // Column lists stay in memory. Row lists are counted and dropped as they arrive.
  const lists = await engine.map(usableCols, (slot, { col }) => engine.documentsOf(slot, col), {
    keyOf: ({ col }) => engine.shardKey(col),
    onDone: step,
  });
  const ready = [];
  usableCols.forEach((item, k) => {
    const list = lists[k];
    if (list === undefined || list.failed) {
      for (const { i } of usableRows) (errors[i] ??= {})[item.j] = list?.failed ?? 'The search stopped first.';
      return;
    }
    totals.cols[item.j] = list.length;
    ready.push({ j: item.j, list });
  });
  // Rows cannot fill any cell without a column, so do not spend the time reading them.
  if (ready.length === 0) {
    if (!engine.stopped) throw new Error(lists.find((list) => list?.failed)?.failed ?? 'no modifier could be read');
    return;
  }
  const finished = await engine.map(usableRows, async (slot, { row, i }) => {
    const list = await engine.documentsOf(slot, row);
    totals.rows[i] = list.length;
    counts[i] ??= {};
    for (const { j, list: other } of ready) counts[i][j] = intersectionSize(list, other);
    return true;
  }, { keyOf: ({ row }) => engine.shardKey(row), onDone: step });
  usableRows.forEach(({ i }, k) => {
    if (!finished[k]?.failed) return;
    errors[i] ??= {};
    for (const { j } of ready) errors[i][j] = finished[k].failed;
  });
}

async function searchRelations(search, tick) {
  const { rows, cols, relation, counts, errors } = search;
  const pairs = [];
  rows.forEach((row, i) => cols.forEach((col, j) => {
    if (row.problem || col.problem) return;
    const single = (term) => term.parts.length === 1 && term.parts[0].phrase === undefined;
    if (!single(row) || !single(col)) {
      (errors[i] ??= {})[j] = 'Relation counts need one entity per side, not a phrase or a list of alternatives.';
      return;
    }
    pairs.push({ row, col, i, j });
  }));
  const keys = [...new Set(pairs.flatMap(({ row, col }) => [relationKey(row.parts[0]), relationKey(col.parts[0])]))];
  tick('Reading relations.', 0, 1);
  $('run-status').textContent = 'Reading the relations file, about 50 MB. The bar moves when it is done.';
  const found = await engine.relationCounts(keys, relation);
  for (const { row, col, i, j } of pairs) {
    const [a, b] = [relationKey(row.parts[0]), relationKey(col.parts[0])].sort();
    (counts[i] ??= {})[j] = found.get(`${a}\n${b}`) ?? 0;
  }
  tick('Reading relations.', 1, 1);
}

function warnBeforeLeaving(event) {
  event.preventDefault();
  event.returnValue = '';
}

async function startSearch() {
  const search = {
    ...checked,
    totals: { rows: {}, cols: {} },
    counts: {},
    errors: {},
    startedAt: new Date().toISOString(),
    stopped: false,
  };
  running = true;
  setBusy(true);
  engine.stopped = false;
  $('checked').hidden = true;
  $('running').hidden = false;
  $('stop').disabled = false;
  $('run-status').textContent = 'Starting.';
  window.addEventListener('beforeunload', warnBeforeLeaving);
  const began = performance.now();
  const tick = (stage, done, total) => reportProgress(stage, done, total, began);
  const work = () => (search.relation ? searchRelations(search, tick) : searchOverlaps(search, tick));
  try {
    // Holding a lock asks the browser not to freeze this tab while it is in the background.
    if (navigator.locks) await navigator.locks.request('pubmatrix-search', work);
    else await work();
  } catch (error) {
    showSearchError(`The search failed: ${error.message ?? error}. Results so far are shown below.`);
    engine.stop();
  }
  search.stopped = engine.stopped;
  window.removeEventListener('beforeunload', warnBeforeLeaving);
  running = false;
  setBusy(false);
  $('running').hidden = true;
  document.title = 'PubMatrix Reloaded';
  const result = buildResult(search);
  const unread = Object.values(search.errors).flatMap((row) => Object.values(row))
    .filter((reason) => !reason.startsWith('Relation counts')).length;
  if (unread > 0) {
    showSearchError(`${number(unread)} cells could not be read, most likely because of a connection problem. `
      + 'They are blank in the results, not zero. Running the search again may fill them.');
  }
  markExample(null);
  showResults(result, 'Your search');
  $('results').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function stopSearch() {
  engine.stop();
  $('stop').disabled = true;
  $('run-status').textContent = 'Stopping after the current terms finish.';
}

// Viewing. Everything from a results file goes in as text, and links are rebuilt.

function headerContent(button, label, total) {
  button.append(label);
  if (total !== null && total !== undefined) button.append(element('small', `${number(total)} docs`));
}

function renderCell(grid, i, j) {
  const cell = grid.cell(i, j);
  const pair = `${grid.rowLabels[i]} × ${grid.colLabels[j]}`;
  if (cell.count === null) {
    const td = element('td', '—', 'none');
    td.title = `${pair}: no value. ${cell.error ?? ''}`;
    return td;
  }
  const step = cell.count > 0 ? heatStep(cell.count, grid.low, grid.high) : 0;
  const td = element('td', null, `h${step}`);
  td.dataset.v = cell.count;
  td.title = `${pair}: ${number(cell.count)} documents`;
  const link = element('a', number(cell.count));
  link.href = cellLink(cell.query);
  link.target = '_blank';
  link.rel = 'noopener';
  td.append(link);
  return td;
}

function renderMatrix(data, grid) {
  const table = $('matrix');
  const head = element('thead');
  const headRow = element('tr');
  const corner = element('th');
  corner.scope = 'col';
  const cornerButton = element('button', 'Term');
  cornerButton.type = 'button';
  cornerButton.append(element('small', 'docs alone'));
  corner.append(cornerButton);
  headRow.append(corner);
  grid.colLabels.forEach((label, j) => {
    const th = element('th');
    th.scope = 'col';
    const button = element('button');
    button.type = 'button';
    headerContent(button, label, data.columns[j].total);
    th.append(button);
    headRow.append(th);
  });
  head.append(headRow);

  const body = element('tbody');
  grid.rowLabels.forEach((label, i) => {
    const row = element('tr');
    const th = element('th');
    th.scope = 'row';
    headerContent(th, label, data.rows[i].total);
    row.append(th);
    let hits = 0;
    grid.colLabels.forEach((_, j) => {
      if (grid.cell(i, j).count > 0) hits += 1;
      row.append(renderCell(grid, i, j));
    });
    row.dataset.hits = hits;
    body.append(row);
  });
  table.replaceChildren(head, body);
  head.querySelectorAll('button').forEach((button) => button.addEventListener('click', () => sortBy(button)));
}

function sortBy(button) {
  const table = $('matrix');
  const th = button.closest('th');
  const col = th.cellIndex;
  const sign = th.getAttribute('aria-sort') === 'descending' ? 1 : -1;
  table.querySelectorAll('thead th').forEach((header) => header.removeAttribute('aria-sort'));
  th.setAttribute('aria-sort', sign < 0 ? 'descending' : 'ascending');
  const value = (row) => {
    const raw = row.cells[col].dataset.v;
    return raw === undefined ? null : Number(raw);
  };
  const body = table.tBodies[0];
  const rows = [...body.rows].sort((a, b) => {
    if (col === 0) return -sign * a.cells[0].textContent.localeCompare(b.cells[0].textContent);
    const x = value(a);
    const y = value(b);
    if (x === null || y === null) return (x === null) - (y === null);
    return sign * (x - y);
  });
  body.append(...rows);
}

function applyHideEmpty() {
  const hide = $('hide-empty').checked;
  for (const row of $('matrix').tBodies[0].rows) row.hidden = hide && row.dataset.hits === '0';
}

function renderTerms(data) {
  const head = element('tr');
  for (const title of ['Input', 'Read as', 'Query', 'Documents', 'Note']) head.append(element('th', title));
  const rows = [head];
  const seen = new Set();
  for (const term of [...data.rows, ...data.columns]) {
    if (seen.has(term.input)) continue;
    seen.add(term.input);
    const row = element('tr');
    row.append(element('td', term.input), element('td', term.biotype ?? term.kind));
    const query = element('td');
    query.append(element('code', term.query));
    const total = term.total === null || term.total === undefined ? '' : number(term.total);
    row.append(query, element('td', total, 'num'), element('td', term.note));
    rows.push(row);
  }
  $('terms-read').replaceChildren(...rows);
}

function renderRunInfo(run) {
  const rows = Object.entries(run ?? {})
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => {
      const row = element('tr');
      row.append(element('th', key.replaceAll('_', ' ')), element('td', String(value)));
      return row;
    });
  $('run-info').replaceChildren(...rows);
}

function renderLegend(grid) {
  const swatches = Array.from({ length: HEAT_STEPS }, (_, n) => element('i', null, `h${n + 1}`));
  $('legend').replaceChildren(
    element('span', number(grid.low)),
    ...swatches,
    element('span', `${number(grid.high)} (log scale; 0 is unshaded)`),
  );
}

const COLOR_KEY = 'pubmatrix-colors';
const COLOR_ENDS = ['low', 'middle', 'high'];

function remembered() {
  try {
    const saved = JSON.parse(localStorage.getItem(COLOR_KEY));
    if (saved && COLOR_PRESETS[saved.preset]) return { preset: saved.preset };
    const colors = saved?.preset === 'custom' && Array.isArray(saved.colors) ? saved.colors.map(parseHex) : [];
    if (colors.length === 3 && !colors.includes(null)) return { preset: 'custom', colors };
  } catch {
    // Storage can be missing or blocked; the default is fine.
  }
  return { preset: 'auto' };
}

function remember(choice) {
  try {
    localStorage.setItem(COLOR_KEY, JSON.stringify(choice));
  } catch {
    // Not remembered, but still shown.
  }
}

/** Set the shades as properties on the results, so the table and the legend both follow. */
function paint(colors) {
  const style = $('results').style;
  if (!colors) {
    for (let n = 1; n <= HEAT_STEPS; n += 1) {
      style.removeProperty(`--h${n}`);
      style.removeProperty(`--ink-h${n}`);
    }
    return;
  }
  heatScale(colors).forEach(({ background, ink }, n) => {
    style.setProperty(`--h${n + 1}`, background);
    style.setProperty(`--ink-h${n + 1}`, ink);
  });
}

function showColors(colors) {
  COLOR_ENDS.forEach((end, n) => {
    $(`color-${end}`).value = colors[n];
    $(`color-${end}-hex`).value = colors[n];
    $(`color-${end}-hex`).removeAttribute('aria-invalid');
  });
  $('color-error').hidden = true;
}

/** The three colors now shown, read from the page's own blue when the scheme is automatic. */
function autoColors() {
  const style = getComputedStyle($('results'));
  return [1, 4, HEAT_STEPS].map((step) => parseHex(style.getPropertyValue(`--h${step}`)) ?? '#888888');
}

function chooseColors(choice) {
  const preset = COLOR_PRESETS[choice.preset];
  const colors = choice.preset === 'custom' ? choice.colors : preset.colors;
  $('color-preset').value = choice.preset;
  paint(colors ?? null);
  showColors(colors ?? autoColors());
  remember(choice.preset === 'custom' ? choice : { preset: choice.preset });
}

function editColor(end, text) {
  const hex = parseHex(text);
  const field = $(`color-${end}-hex`);
  if (!hex) {
    field.setAttribute('aria-invalid', 'true');
    $('color-error').textContent = `"${text}" is not a hex color. Use six digits such as #0d366b, or three such as #abc.`;
    $('color-error').hidden = false;
    return;
  }
  const colors = COLOR_ENDS.map((other) => (other === end ? hex : parseHex($(`color-${other}-hex`).value)));
  if (colors.includes(null)) return;
  chooseColors({ preset: 'custom', colors });
}

function setupColors() {
  addOptions($('color-preset'), [
    ...Object.entries(COLOR_PRESETS).map(([key, preset]) => [key, preset.label]),
    ['custom', 'Custom (your own colors)'],
  ]);
  $('color-preset').addEventListener('change', (event) => {
    const preset = event.target.value;
    if (preset !== 'custom') chooseColors({ preset });
    else $('color-low-hex').focus();
  });
  for (const end of COLOR_ENDS) {
    $(`color-${end}`).addEventListener('input', (event) => editColor(end, event.target.value));
    $(`color-${end}-hex`).addEventListener('change', (event) => editColor(end, event.target.value));
  }
  chooseColors(remembered());
}

function showResults(data, name, reading = '') {
  const problem = resultProblem(data);
  $('load-error').hidden = !problem;
  if (problem) {
    $('load-error').textContent = problem;
    $('results').hidden = true;
    return;
  }
  const grid = toGrid(data);
  const blanks = data.cells.filter((cell) => cell.count === null).length;
  const relation = data.run?.relation;
  const what = relation
    ? `documents with ${relation === 'ANY' ? 'any extracted relation' : `the relation "${relation}"`}`
    : 'documents mentioning both terms';
  $('summary').textContent = `${name}: ${number(data.rows.length)} × ${number(data.columns.length)}, counting ${what}`;
  $('reading').textContent = reading;
  $('reading').hidden = !reading;
  const partial = String(data.run?.status ?? '').startsWith('partial');
  $('partial').hidden = !partial && blanks === 0;
  const again = data.run?.command ? ' Run the same command again to continue.' : '';
  $('partial').textContent = partial
    ? `Partial result: ${number(blanks)} of ${number(data.cells.length)} cells have no value.${again}`
    : `${number(blanks)} of ${number(data.cells.length)} cells have no value. A blank is not a zero; hover for the reason.`;
  renderMatrix(data, grid);
  renderLegend(grid);
  renderTerms(data);
  renderRunInfo(data.run);
  $('hide-empty').checked = false;
  $('results').hidden = false;
  shown = data;
}

function showLoadError(message) {
  $('load-error').textContent = message;
  $('load-error').hidden = false;
  $('results').hidden = true;
}

function markExample(id) {
  for (const button of $('examples').children) {
    button.setAttribute('aria-pressed', String(button.dataset.id === id));
  }
}

async function openFile(file) {
  if (!file) return;
  markExample(null);
  try {
    showResults(JSON.parse(await file.text()), file.name);
  } catch {
    showLoadError(`${file.name} could not be read as JSON. Choose the .json file the tool wrote.`);
  }
}

async function openExample(example) {
  markExample(example.id);
  try {
    const response = await fetch(`examples/${example.id}.json`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    showResults(await response.json(), example.title, example.reading);
    $('summary').focus({ preventScroll: true });
    $('results').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch {
    showLoadError('This example could not be loaded.');
  }
}

function renderExamples() {
  const buttons = EXAMPLES.map((example) => {
    const button = element('button');
    button.type = 'button';
    button.dataset.id = example.id;
    button.setAttribute('aria-pressed', 'false');
    button.append(element('b', example.title), element('small', example.question));
    button.addEventListener('click', () => openExample(example));
    return button;
  });
  $('examples').replaceChildren(...buttons);
}

function watchDrops() {
  const zone = $('drop');
  for (const type of ['dragenter', 'dragover']) {
    zone.addEventListener(type, (event) => { event.preventDefault(); zone.classList.add('over'); });
  }
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', (event) => {
    event.preventDefault();
    zone.classList.remove('over');
    openFile(event.dataTransfer.files[0]);
  });
}

function start() {
  const types = [['', 'Any type'], ...CONCEPTS.map((concept) => [concept, concept]), ['phrase', 'phrases only']];
  // Options are added before the first update, since it reads them.
  addOptions($('terms-type'), types);
  addOptions($('modifiers-type'), types);
  addOptions($('relation'), [
    ['', 'Co-occurrence (both terms mentioned)'],
    ...RELATION_TYPES.map((type) => [type, type === 'ANY' ? 'Any extracted relation' : `Relation: ${type}`]),
  ]);
  $('plan').addEventListener('input', updatePlan);
  $('command-line').addEventListener('input', updatePlan);
  $('plan').addEventListener('submit', (event) => event.preventDefault());
  const saveList = (name, terms) => download(name, `${terms.join('\n')}\n`);
  $('save-terms').addEventListener('click', () => saveList(TERMS_FILE, currentPlan().terms));
  $('save-modifiers').addEventListener('click', () => saveList(MODIFIERS_FILE, currentPlan().modifiers));
  $('check').addEventListener('click', checkTerms);
  $('start').addEventListener('click', startSearch);
  $('stop').addEventListener('click', stopSearch);
  $('save-json').addEventListener('click', () => {
    download('pubmatrix.json', `${JSON.stringify(shown, null, 2)}\n`, 'application/json');
  });
  $('save-csv').addEventListener('click', () => download('pubmatrix.csv', toCsv(shown), 'text/csv'));
  $('data-release').textContent = DATA_RELEASE;
  $('dataset-link').href = `https://huggingface.co/datasets/${DATASET}`;
  $('copy').addEventListener('click', copyCommand);
  $('file').addEventListener('change', (event) => openFile(event.target.files[0]));
  $('hide-empty').addEventListener('change', applyHideEmpty);
  setupColors();
  renderExamples();
  watchDrops();
  updatePlan();
}

start();
