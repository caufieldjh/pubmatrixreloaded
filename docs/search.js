// Runs searches in the browser with DuckDB WASM, reading Parquet from Hugging Face.
// The PubTator3 API refuses browsers, but these files allow range requests from any
// site. See issue 3 for the measurements behind these choices.

import {
  DATASET, DATASET_REVISION, DUCKDB_VERSION, PHRASE_API, PHRASE_MAX_DOCS, PHRASE_PAGE_SIZE,
  PHRASE_REQUESTS, SEARCH_WORKERS, classifyPart, phraseQuery, relationKey, shardIndex,
  sortedUnion, spellGreek, splitParts, yearSlices,
} from './core.js';

const DATA_URL = `https://huggingface.co/datasets/${DATASET}/resolve/${DATASET_REVISION}/`;
const DUCKDB_URL = `https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@${DUCKDB_VERSION}/+esm`;
const RETRIES = 4;

// Europe PMC often answers 503, and a request can hang, so pages need many chances.
const PAGE_ATTEMPTS = 8;
const PAGE_TIMEOUT_MS = 15000;

// Other meanings rarer than this are mostly tagging noise, not worth a reader's time.
const MEANINGFUL_SHARE = 0.05;

const quote = (value) => `'${value.replaceAll("'", "''")}'`;
const pause = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

class Slot {
  constructor(duckdb, db, conn) {
    this.duckdb = duckdb;
    this.db = db;
    this.conn = conn;
    this.registered = new Set();
    this.generation = 0;
  }

  // Each query names one small file: DuckDB WASM walks a file's whole row group list
  // per query, which takes about a second in a large file.
  shardNumber(name, shards, value) {
    return String(shardIndex(shards[name], value)).padStart(3, '0');
  }

  async shard(name, shards, value) {
    const number = this.shardNumber(name, shards, value);
    return this.register(`${name}-${number}`, `${DATA_URL}${name}/${number}.parquet`);
  }

  async register(stem, url) {
    const file = `${stem}-${this.generation}.parquet`;
    if (!this.registered.has(file)) {
      this.registered.add(file);
      await this.db.registerFileURL(file, url, this.duckdb.DuckDBDataProtocol.HTTP, false);
    }
    return file;
  }

  // DuckDB remembers a bad read, so a retry must open the file under a new name.
  startClean() {
    this.generation += 1;
  }
}

export class Engine {
  static async start() {
    const duckdb = await import(DUCKDB_URL);
    const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
    // A worker must come from this site, so it loads DuckDB's script from a blob.
    const script = new Blob([`importScripts("${bundle.mainWorker}");`], { type: 'text/javascript' });
    const workerUrl = URL.createObjectURL(script);
    const [shards, ...slots] = await Promise.all([
      fetch(`${DATA_URL}shards.json`).then((response) => {
        if (!response.ok) throw new Error(`shards.json: HTTP ${response.status}`);
        return response.json();
      }),
      ...Array.from({ length: SEARCH_WORKERS }, async () => {
        const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), new Worker(workerUrl));
        await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
        // Left to its defaults, this build downloads whole files instead of reading ranges.
        await db.open({
          filesystem: { reliableHeadRequests: true, allowFullHTTPReads: false, forceFullHTTPReads: false },
        });
        return new Slot(duckdb, db, await db.connect());
      }),
    ]);
    URL.revokeObjectURL(workerUrl);
    return new Engine(shards, slots);
  }

  constructor(shards, slots) {
    this.shards = shards;
    this.slots = slots;
    this.stopped = false;
    this.phraseLists = new Map();
  }

  stop() {
    this.stopped = true;
  }

  /**
   * Run work on every item, a few at a time. Items not reached stay undefined.
   *
   * Items with the same key run one after another on one worker. Concurrent range
   * requests for one URL returned bad bytes in Chromium, so no two workers may read
   * the same file at once.
   */
  async map(items, work, { keyOf = (_, index) => index, onDone } = {}) {
    const groups = new Map();
    items.forEach((item, index) => {
      const key = keyOf(item, index);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(index);
    });
    const queue = [...groups.values()];
    const results = new Array(items.length);
    let done = 0;
    await Promise.all(this.slots.map(async (slot) => {
      while (queue.length > 0 && !this.stopped) {
        for (const index of queue.shift()) {
          if (this.stopped) break;
          results[index] = await this.withRetries(slot, () => work(slot, items[index]));
          done += 1;
          onDone?.(done, items.length);
        }
      }
    }));
    return results;
  }

  async withRetries(slot, work) {
    for (let attempt = 1; ; attempt += 1) {
      try {
        return await work();
      } catch (error) {
        if (error.final || attempt >= RETRIES) return { failed: String(error.message ?? error).slice(0, 200) };
        slot.startClean();
        await pause(1000 * 2 ** attempt);
      }
    }
  }

  /** Read every term, one query per names file. */
  async resolveAll(requests, onDone) {
    const plans = requests.map(({ raw, type }) => ({
      raw,
      type,
      parts: splitParts(raw).map((text) => ({ text, ...classifyPart(text, type) })),
    }));
    const spellings = new Set();
    for (const { parts } of plans) {
      for (const { name } of parts) {
        if (name) {
          spellings.add(name);
          spellings.add(spellGreek(name));
        }
      }
    }
    const byShard = new Map();
    for (const spelling of spellings) {
      const shard = shardIndex(this.shards.names, spelling);
      if (!byShard.has(shard)) byShard.set(shard, []);
      byShard.get(shard).push(spelling);
    }
    const groups = [...byShard.values()];
    const results = await this.map(groups, (slot, names) => this.readNames(slot, names), { onDone });
    const rows = new Map();
    const failures = new Map();
    groups.forEach((names, index) => {
      const result = results[index];
      for (const name of names) {
        if (result === undefined || result.failed) failures.set(name, result?.failed ?? 'the check was stopped');
        else rows.set(name, result.get(name) ?? []);
      }
    });
    const terms = plans.map((plan) => this.assemble(plan, rows, failures));
    await this.countPhrases(terms);
    return terms;
  }

  /** Fetch one page from Europe PMC, trying again as long as it is worth it. */
  async phrasePage(query, cursor, size) {
    const url = new URL(PHRASE_API);
    url.search = new URLSearchParams({
      query, format: 'json', resultType: 'idlist', pageSize: size, cursorMark: cursor,
    });
    let problem = '';
    for (let attempt = 1; attempt <= PAGE_ATTEMPTS && !this.stopped; attempt += 1) {
      try {
        const response = await fetch(url, { signal: AbortSignal.timeout(PAGE_TIMEOUT_MS) });
        if (response.ok) return await response.json();
        problem = `HTTP ${response.status}`;
      } catch (error) {
        problem = error.name === 'TimeoutError' ? 'no answer' : String(error.message ?? error);
      }
      await pause(Math.min(500 * 2 ** attempt, 8000));
    }
    throw new Error(this.stopped ? 'the search was stopped' : `Europe PMC: ${problem}`);
  }

  /** Learn how many documents each phrase has, which sets how long it will take. */
  async countPhrases(terms) {
    const parts = terms.flatMap((term) => term.parts.filter((part) => part.phrase !== undefined));
    const unique = [...new Set(parts.map((part) => part.phrase))];
    const counts = new Map();
    let next = 0;
    const failures = new Map();
    await Promise.all(Array.from({ length: PHRASE_REQUESTS }, async () => {
      while (next < unique.length) {
        const phrase = unique[next];
        next += 1;
        try {
          const page = await this.phrasePage(phraseQuery(phrase), '*', 1);
          counts.set(phrase, page.hitCount);
        } catch (error) {
          failures.set(phrase, error.message);
        }
      }
    }));
    for (const term of terms) {
      for (const part of term.parts) {
        if (part.phrase === undefined) continue;
        part.hits = counts.get(part.phrase);
        if (failures.has(part.phrase)) {
          term.unchecked = true;
          term.problem = `Could not be checked: ${failures.get(part.phrase)}`;
        } else if (part.hits > PHRASE_MAX_DOCS) {
          term.problem = `"${part.phrase}" has ${part.hits.toLocaleString('en-US')} documents, too many to fetch `
            + `here (the limit is ${PHRASE_MAX_DOCS.toLocaleString('en-US')}). The command line tool can search it.`;
        }
      }
      if (term.problem) term.parts = [];
    }
  }

  /**
   * Fetch whole document lists for phrases. A list arrives whole or not at all, because
   * a partial list would give counts that look real and are too low.
   */
  async fetchPhrases(phrases, onDocuments) {
    const year = new Date().getFullYear();
    const lists = new Map();
    const failures = new Map();
    const jobs = [];
    for (const { phrase, hits } of phrases) {
      if (this.phraseLists.has(phrase)) {
        onDocuments(hits);
        continue;
      }
      lists.set(phrase, []);
      for (const years of yearSlices(hits, year)) jobs.push({ phrase, years });
    }
    await Promise.all(Array.from({ length: PHRASE_REQUESTS }, async () => {
      while (jobs.length > 0 && !this.stopped) {
        const job = jobs.shift();
        if (failures.has(job.phrase)) continue;
        try {
          await this.fetchSlice(job, lists.get(job.phrase), onDocuments);
        } catch (error) {
          failures.set(job.phrase, error.message);
        }
      }
    }));
    for (const [phrase, parts] of lists) {
      if (failures.has(phrase) || this.stopped) continue;
      this.phraseLists.set(phrase, sortedUnion(parts));
    }
    return failures;
  }

  async fetchSlice({ phrase, years }, parts, onDocuments) {
    const query = phraseQuery(phrase, years);
    let cursor = '*';
    for (;;) {
      const page = await this.phrasePage(query, cursor, PHRASE_PAGE_SIZE);
      const results = page.resultList?.result ?? [];
      const ids = results.map((result) => Number(result.pmid)).filter(Number.isInteger);
      parts.push(Int32Array.from(ids).sort());
      onDocuments(results.length);
      const finished = results.length < PHRASE_PAGE_SIZE || page.nextCursorMark === cursor;
      if (finished) return;
      cursor = page.nextCursorMark;
    }
  }

  async readNames(slot, names) {
    const file = await slot.shard('names', this.shards, names[0]);
    const result = await slot.conn.query(`SELECT name, type, concept, n FROM '${file}'
      WHERE name IN (${names.map(quote).join(',')})`);
    const found = new Map();
    for (const row of result.toArray()) {
      if (!found.has(row.name)) found.set(row.name, []);
      found.get(row.name).push({
        type: row.type.toLowerCase(), concept: row.concept, mentions: Number(row.n),
      });
    }
    return found;
  }

  assemble({ raw, type, parts }, rows, failures) {
    const read = [];
    for (const part of parts) {
      if (part.problem) return { raw, problem: part.problem, parts: [] };
      if (part.phrase !== undefined) {
        read.push({ text: part.text, phrase: part.phrase, type: 'phrase', alternatives: [] });
        continue;
      }
      if (part.id) {
        read.push({ text: part.text, concept: part.id, type: part.type, alternatives: [] });
        continue;
      }
      const spellings = [...new Set([part.name, spellGreek(part.name)])];
      const failure = spellings.map((name) => failures.get(name)).find(Boolean);
      if (failure) return { raw, unchecked: true, problem: `Could not be checked: ${failure}`, parts: [] };
      const best = this.bestMeaning(spellings.flatMap((name) => rows.get(name)), type);
      if (!best) {
        // Not an entity, so it can only be searched as the words themselves.
        read.push({ text: part.text, phrase: part.text, type: 'phrase', alternatives: [] });
        continue;
      }
      if (!read.some((seen) => seen.type === best.type && seen.concept === best.concept)) {
        read.push({ text: part.text, ...best });
      }
    }
    return { raw, parts: read };
  }

  bestMeaning(found, type) {
    const totals = new Map();
    for (const meaning of found) {
      if (type && meaning.type !== type) continue;
      const key = `${meaning.type}|${meaning.concept}`;
      const seen = totals.get(key)?.mentions ?? 0;
      totals.set(key, { ...meaning, mentions: seen + meaning.mentions });
    }
    if (totals.size === 0) return null;
    const [best, ...others] = [...totals.values()].sort((a, b) => b.mentions - a.mentions);
    const alternatives = others.filter((other) => other.mentions >= best.mentions * MEANINGFUL_SHARE);
    return { ...best, alternatives };
  }

  /** Terms in one file share a key, so that one worker reads that file. */
  shardKey(term) {
    const entity = term.parts.find((part) => part.phrase === undefined);
    if (!entity) return term.raw;
    return `${entity.type}-${shardIndex(this.shards[entity.type], entity.concept)}`;
  }

  async documentsOf(slot, term) {
    const lists = [];
    for (const part of term.parts) {
      if (part.phrase !== undefined) {
        const list = this.phraseLists.get(part.phrase);
        if (!list) throw Object.assign(new Error(`The list for "${part.phrase}" did not arrive.`), { final: true });
        lists.push(list);
        continue;
      }
      const file = await slot.shard(part.type, this.shards, part.concept);
      const result = await slot.conn.query(`SELECT pmid FROM '${file}'
        WHERE concept = ${quote(part.concept)}`);
      lists.push(result.getChild('pmid')?.toArray() ?? new Int32Array());
    }
    return sortedUnion(lists.map((list) => Int32Array.from(list).sort()));
  }

  /** Read every pair in one pass. Keys are entity keys; returns a map of "a|b" to counts. */
  async relationCounts(keys, relation) {
    const [slot] = this.slots;
    const typeTest = relation === 'ANY' ? '' : ` AND type = ${quote(relation)}`;
    const list = keys.map(quote).join(',');
    const result = await this.withRetries(slot, async () => {
      const file = await slot.register('relations', `${DATA_URL}relations.parquet`);
      return slot.conn.query(`SELECT a, b, sum(n)::INTEGER AS n FROM '${file}'
        WHERE a IN (${list}) AND b IN (${list})${typeTest} GROUP BY a, b`);
    });
    if (result.failed) throw new Error(result.failed);
    return new Map(result.toArray().map((row) => [`${row.a}\n${row.b}`, row.n]));
  }

  async close() {
    await Promise.all(this.slots.map(async (slot) => {
      await slot.conn.close();
      await slot.db.terminate();
    }));
  }
}
