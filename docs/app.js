import {
  CONCEPTS, EXAMPLES, HEAT_STEPS, LARGE_RUN_REQUESTS, MODIFIERS_FILE, RELATION_TYPES, TERMS_FILE,
  buildCommand, cellLink, estimate, formatDuration, heatStep, parseTerms, resultProblem,
  termsUnfitForRelations, termsWithStrayQuotes, toGrid,
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
  $('save-terms').disabled = plan.terms.length === 0;
  $('save-modifiers').disabled = plan.modifiers.length === 0;
}

function download(name, lines) {
  const url = URL.createObjectURL(new Blob([`${lines.join('\n')}\n`], { type: 'text/plain' }));
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

// Viewing. Everything from a results file goes in as text, and links are rebuilt.

function headerContent(button, label, total) {
  button.append(label, element('small', total === null || total === undefined ? '? docs' : `${number(total)} docs`));
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
  $('partial').textContent = partial
    ? `Partial result: ${number(blanks)} of ${number(data.cells.length)} cells have no value. Run the same command again to continue.`
    : `${number(blanks)} of ${number(data.cells.length)} cells have no value. A blank is not a zero; hover for the reason.`;
  renderMatrix(data, grid);
  renderLegend(grid);
  renderTerms(data);
  renderRunInfo(data.run);
  $('hide-empty').checked = false;
  $('results').hidden = false;
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
  const types = [['', 'Any type'], ...CONCEPTS.map((concept) => [concept, concept])];
  addOptions($('terms-type'), types);
  addOptions($('modifiers-type'), types);
  addOptions($('relation'), [
    ['', 'Co-occurrence (both terms mentioned)'],
    ...RELATION_TYPES.map((type) => [type, type === 'ANY' ? 'Any extracted relation' : `Relation: ${type}`]),
  ]);
  $('plan').addEventListener('input', updatePlan);
  $('plan').addEventListener('submit', (event) => event.preventDefault());
  $('save-terms').addEventListener('click', () => download(TERMS_FILE, currentPlan().terms));
  $('save-modifiers').addEventListener('click', () => download(MODIFIERS_FILE, currentPlan().modifiers));
  $('copy').addEventListener('click', copyCommand);
  $('file').addEventListener('change', (event) => openFile(event.target.files[0]));
  $('hide-empty').addEventListener('change', applyHideEmpty);
  renderExamples();
  watchDrops();
  updatePlan();
}

start();
