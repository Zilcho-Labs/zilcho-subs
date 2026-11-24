// Runs the real front-end scripts (listview.js, libraries.js, library.js, monitor.js, coverage.js)
// against a stub page and a stub api, to check the 0.13.0 Library / Library status logic.
// Usage: node tests/library_page_check.js <static folder>. Prints {"passed": [...]} or throws.
// tests/test_library_page.py runs it under pytest when Node is installed.
const vm = require('vm'), fs = require('fs'), path = require('path'), assert = require('assert');
const STATIC = process.argv[2];
const passed = [];
// Values made inside the script's own context have other prototypes, so compare them as JSON.
const deep = (a, b, message) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b), message);
const test = async (name, fn) => { await fn(); passed.push(name); };

function fakeEl(tag) {
  const e = { tag, value: '', checked: false, hidden: false, textContent: '', dataset: {}, disabled: false, className: '', children: [], style: {}, open: false, title: '',
    classList: { toggle() {}, add() {}, remove() {} }, setAttribute() {}, append(...a) { e.children.push(...a); }, prepend() {}, replaceChildren(...a) { e.children = a; },
    querySelector() { return fakeEl('x'); }, addEventListener() {}, focus() {}, showModal() {}, close() {} };
  return e;
}

// A fresh page: loads the scripts the way index.html does (in order) and records what they do.
function page(scripts) {
  const els = {}, store = {}, calls = [], toasts = [], lists = {};
  const answers = {};   // 'METHOD path' -> value or function(body) -> value
  const ctx = vm.createContext({
    console, encodeURIComponent, JSON, Promise, Map, Set, Math, Number, String, Array, Object, Date, Error, RegExp,
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } },
    document: { getElementById: id => (id === 'browse-form' ? {} : null), querySelectorAll: () => [], querySelector: () => fakeEl('x'), createElement: t => fakeEl(t), addEventListener() {}, hidden: false, title: '' },
    confirm: () => true,
  });
  ctx.$ = s => els[s] || (els[s] = fakeEl('x'));
  ctx.node = (tag, text, cls) => { const e = fakeEl(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  ctx.action = (text, cb, cls) => ({ text, cb, cls });
  ctx.toast = t => toasts.push(t);
  ctx.byName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
  ctx.langName = c => c;
  ctx.state = { settings: { target_languages: ['en', 'es'] }, languages: { en: 'English', es: 'Spanish' } };
  ctx.view = 'library'; ctx.settingsLoaded = true; ctx.queueTab = 'jobs';
  ctx.refresh = async () => {}; ctx.chooseView = () => {}; ctx.loadQuality = () => {};
  ctx.api = async (p, body, method) => {
    const key = (method || (body === undefined ? 'GET' : 'POST')) + ' ' + p;
    calls.push({ key, body });
    const a = answers[key];
    if (a === undefined) throw Error('unexpected request ' + key);
    return typeof a === 'function' ? a(body) : a;
  };
  const real = f => fs.readFileSync(path.join(STATIC, f), 'utf8');
  const run = f => vm.runInContext(real(f), ctx, { filename: f });
  run('listview.js');
  ctx.listView = o => { lists[o.name] = o; return { set: async () => {}, resetSearch() {}, refilter() {}, update: async () => {}, clear() {}, redraw() {} }; };
  for (const f of scripts) run(f);
  return { ctx, els, calls, toasts, lists, answers, ev: code => vm.runInContext(code, ctx) };
}

const V = (name, extra = {}) => ({ path: 'tv/' + name, name, directory: false, subtitle: false, status: { subs: [], missing: ['en', 'es'] }, ...extra });
const S = (name, extra = {}) => ({ path: 'music/' + name, name, directory: false, status: { lyrics: false, enhanced: false, files: [] }, ...extra });
const D = name => ({ path: 'tv/' + name, name, directory: true });
const sleep = () => new Promise(r => setImmediate(r));

(async () => {
  // ----- Library page -----
  await test('browse merges both listings: folders once, each file with its kind', async () => {
    const p = page(['libraries.js', 'library.js']);
    p.answers['GET /browse?path='] = { entries: [D('Show'), V('a.mkv'), V('a.en.srt', { subtitle: true, status: undefined })], truncated: false };
    p.answers['GET /music/browse?path='] = { entries: [D('Show'), D('Album'), S('song.flac')], truncated: false };
    await p.ev('browse()');
    const entries = p.ev('libraryEntries');
    deep(entries.map(e => e.kind + ':' + e.name), ['folder:Show', 'video:a.mkv', 'subtitle:a.en.srt', 'folder:Album', 'song:song.flac']);
  });

  await test('chips: Songs hides videos and subtitle files but keeps folders; Videos hides songs', async () => {
    const p = page(['libraries.js', 'library.js']);
    p.answers['GET /browse?path='] = { entries: [D('Show'), V('a.mkv'), V('a.en.srt', { subtitle: true, status: undefined })], truncated: false };
    p.answers['GET /music/browse?path='] = { entries: [S('song.flac')], truncated: false };
    await p.ev('browse()');
    const names = async kind => { p.ev(`libraryKind=${JSON.stringify(kind)}`); return (await p.lists.library.server({ q: '', filter: '', sort: 'name', offset: 0, limit: 20 })).items.map(e => e.name); };
    deep(await names(''), ['Show', 'a.en.srt', 'a.mkv', 'song.flac']);
    deep(await names('song'), ['Show', 'song.flac']);
    deep(await names('video'), ['Show', 'a.en.srt', 'a.mkv']);
  });

  await test('filters apply only to their own kind; pickable counts ignore folders and subtitle files', async () => {
    const p = page(['libraries.js', 'library.js']);
    const done = V('done.mkv', { status: { subs: ['en', 'es'], missing: [] } });
    p.answers['GET /browse?path='] = { entries: [D('Show'), V('a.mkv'), done, V('a.en.srt', { subtitle: true, status: undefined })], truncated: false };
    p.answers['GET /music/browse?path='] = { entries: [S('s1.flac'), S('s2.flac', { status: { lyrics: true, enhanced: false, files: [] } })], truncated: false };
    await p.ev('browse()');
    const run = async filter => p.lists.library.server({ q: '', filter, sort: 'name', offset: 0, limit: 20 });
    deep((await run('v:missing')).items.map(e => e.name), ['Show', 'a.mkv']);
    deep((await run('s:missing')).items.map(e => e.name), ['Show', 's1.flac']);
    deep((await run('s:has')).items.map(e => e.name), ['Show', 's2.flac']);
    deep((await run('v:missing:es')).items.map(e => e.name), ['Show', 'a.mkv']);
    await run('');
    assert.strictEqual(p.lists.library.pickableTotal(), 4);   // a.mkv, done.mkv, s1, s2 (not Show, not the .srt)
  });

  await test('paging is by the page size and the total counts every match', async () => {
    const p = page(['libraries.js', 'library.js']);
    p.answers['GET /browse?path='] = { entries: Array.from({ length: 7 }, (_, i) => V('v' + i + '.mkv')), truncated: false };
    p.answers['GET /music/browse?path='] = { entries: [], truncated: false };
    await p.ev('browse()');
    const r = await p.lists.library.server({ q: '', filter: '', sort: 'name', offset: 5, limit: 5 });
    assert.strictEqual(r.total, 7); deep(r.items.map(e => e.name), ['v5.mkv', 'v6.mkv']);
  });

  await test('search asks the server for the chosen library and kind, once per query', async () => {
    const p = page(['libraries.js', 'library.js']);
    p.ev("libraries=[{id:'tv1',name:'TV',folders:['tv'],whole_folder:false}];setLibraryChoice('tv1');libraryKind='video'");
    const hit = { entries: [{ ...V('Naruto 01.mkv'), kind: 'video', folder: 'tv/Naruto' }], total: 1, truncated: false, max_files: 100000 };
    p.answers['GET /library/search?q=naru&kind=video&limit=500&library=tv1'] = hit;
    const q = { q: 'naru', filter: '', sort: 'name', offset: 0, limit: 20 };
    const r = await p.lists.library.server(q);
    await p.lists.library.server({ ...q, offset: 20 });   // paging or a filter change must not search again
    assert.strictEqual(r.total, 1); assert.strictEqual(r.items[0].folder, 'tv/Naruto');
    assert.strictEqual(p.calls.filter(c => c.key.startsWith('GET /library/search')).length, 1);
  });

  await test('search keeps the server order on the default sort and says when results were cut', async () => {
    const p = page(['libraries.js', 'library.js']);
    const e = (n, k) => ({ ...(k === 'song' ? S(n) : V(n)), kind: k });
    p.answers['GET /library/search?q=x&kind=&limit=500'] = { entries: [e('z best.mkv', 'video'), e('a worse.flac', 'song')], total: 900, truncated: true, max_files: 100000 };
    const r = await p.lists.library.server({ q: 'x', filter: '', sort: 'name', offset: 0, limit: 20 });
    deep(r.items.map(i => i.name), ['z best.mkv', 'a worse.flac']);
    assert.ok(/best 2 of 900/.test(p.els['#search-note'].textContent) && /100,000/.test(p.els['#search-note'].textContent));
    assert.strictEqual(p.els['#search-note'].hidden, false);
    const byName = await p.lists.library.server({ q: 'x', filter: '', sort: 'name_desc', offset: 0, limit: 20 });
    deep(byName.items.map(i => i.name), ['z best.mkv', 'a worse.flac']);
    const asc = (await p.lists.library.server({ q: 'x', filter: 's:missing', sort: 'name', offset: 0, limit: 20 })).items;
    deep(asc.map(i => i.name), ['a worse.flac']);
  });

  await test('a mixed selection becomes one batch per kind and one message that counts what was queued', async () => {
    const p = page(['libraries.js', 'library.js']);
    p.answers['GET /browse?path='] = { entries: [V('a.mkv'), V('b.mkv')], truncated: false };
    p.answers['GET /music/browse?path='] = { entries: [S('s.flac')], truncated: false };
    await p.ev('browse()');
    await p.lists.library.server({ q: '', filter: '', sort: 'name', offset: 0, limit: 20 });
    p.els['#audio-stream'] = Object.assign(fakeEl('i'), { value: '2' });
    p.els['#lyrics-regenerate'] = Object.assign(fakeEl('i'), { checked: true });
    p.answers['POST /jobs/batch'] = b => ({ ids: b.paths.slice(0, 1), count: 1, errors: [{ path: b.paths[1], error: 'Queue is full' }] });
    p.answers['POST /lyrics/batch'] = b => ({ ids: ['x'], count: b.paths.length, errors: [] });
    const keys = ['tv/a.mkv', 'tv/b.mkv', 'music/s.flac'];
    const [button] = p.lists.library.bulk(keys, { clear() {} });
    assert.strictEqual(button.text, 'Make subtitles & lyrics for selected (3)');
    await button.cb();
    const vid = p.calls.find(c => c.key === 'POST /jobs/batch'), song = p.calls.find(c => c.key === 'POST /lyrics/batch');
    deep(vid.body.paths, ['tv/a.mkv', 'tv/b.mkv']); assert.strictEqual(vid.body.audio_stream, 2);
    deep(song.body, { paths: ['music/s.flac'], regenerate: true });
    assert.ok(/1 of 2 videos added/.test(p.toasts[0]) && /1 song added/.test(p.toasts[0]), p.toasts[0]);
  });

  await test('one-kind selections are labelled for their kind, and Make for all uses the shown counts', async () => {
    const p = page(['libraries.js', 'library.js']);
    p.answers['GET /browse?path='] = { entries: [V('a.mkv')], truncated: false };
    p.answers['GET /music/browse?path='] = { entries: [S('s.flac')], truncated: false };
    await p.ev('browse()');
    await p.lists.library.server({ q: '', filter: '', sort: 'name', offset: 0, limit: 20 });
    assert.strictEqual(p.lists.library.bulk(['tv/a.mkv'], {})[0].text, 'Make subtitles for selected (1)');
    assert.strictEqual(p.lists.library.bulk(['music/s.flac'], {})[0].text, 'Make lyrics for selected (1)');
    const all = p.lists.library.all({ pickable: 2, filter: '', query: '', keys: async () => [] });
    assert.strictEqual(all[0].text, 'Make subtitles & lyrics for all 2');
    deep(p.lists.library.all({ pickable: 0 }), []);
    const keys = await p.lists.library.serverKeys({ q: '', filter: '', sort: 'name' });
    deep(keys.sort(), ['music/s.flac', 'tv/a.mkv']);
  });

  await test('switching library while a folder is loading keeps the newest answer only', async () => {
    const p = page(['libraries.js', 'library.js']);
    let release;
    p.answers['GET /browse?path=&library=old'] = () => new Promise(r => { release = () => r({ entries: [V('old.mkv')], truncated: false }); });
    p.answers['GET /music/browse?path=&library=old'] = { entries: [], truncated: false };
    p.answers['GET /browse?path=&library=new'] = { entries: [V('new.mkv')], truncated: false };
    p.answers['GET /music/browse?path=&library=new'] = { entries: [], truncated: false };
    p.ev("libraries=[{id:'old',name:'Old',folders:['a'],whole_folder:false},{id:'new',name:'New',folders:['b'],whole_folder:false}];setLibraryChoice('old')");
    const first = p.ev('browse()');
    await sleep();
    p.ev("setLibraryChoice('new')");
    await p.ev('browse()');
    release(); await first;
    deep(p.ev('libraryEntries').map(e => e.name), ['new.mkv']);
  });

  await test('the kind chips remember their choice and ignore nonsense', async () => {
    const p = page(['libraries.js', 'library.js']);
    await p.ev("chooseKind('song')");
    assert.strictEqual(p.ev('libraryKind'), 'song');
    await p.ev("chooseKind('bogus')");
    assert.strictEqual(p.ev('libraryKind'), '');
  });

  // ----- Library status -----
  const status = (video, song) => ({ summary: { status: 'off', generated: 0, failed: video.failed + song.failed, missing: 0, pending_jobs: 0, last_scan: 0, watcher_enabled: false, next_scan: 0, last_error: '', counts: { video, song }, servers: [] } });
  const zero = { missing: 0, queued: 0, complete: 0, failed: 0, waiting: 0 };

  await test('Retry N failed files shows its count and hides at 0', async () => {
    const p = page(['libraries.js', 'library.js', 'coverage.js', 'monitor.js']);
    p.ev('renderMonitorView(' + JSON.stringify(status({ ...zero, failed: 2 }, { ...zero, failed: 1 })) + ')');
    assert.strictEqual(p.els['#monitor-retry-failed'].hidden, false);
    assert.strictEqual(p.els['#monitor-retry-failed'].textContent, 'Retry 3 failed files');
    p.ev('renderMonitorView(' + JSON.stringify(status({ ...zero, failed: 1 }, zero)) + ')');
    assert.strictEqual(p.els['#monitor-retry-failed'].textContent, 'Retry 1 failed file');
    p.ev('renderMonitorView(' + JSON.stringify(status(zero, zero)) + ')');
    assert.strictEqual(p.els['#monitor-retry-failed'].hidden, true);
  });

  await test('Retry sends exactly the failed files of the chosen library, and the message counts them', async () => {
    const p = page(['libraries.js', 'library.js', 'coverage.js', 'monitor.js']);
    p.ev("libraries=[{id:'tv1',name:'TV',folders:['tv'],whole_folder:false}];setLibraryChoice('tv1')");
    p.answers['GET /state/audit?state=failed&paths=true&library=tv1'] = { summary: {}, total: 2, paths: ['tv/a.mkv', 'tv/b.mkv'] };
    p.answers['POST /audit/batch'] = b => ({ queued: b.paths.length, errors: [] });
    p.answers['GET /state/audit?items=false&library=tv1'] = status(zero, zero);
    await p.els['#monitor-retry-failed'].onclick();
    deep(p.calls.find(c => c.key === 'POST /audit/batch').body, { paths: ['tv/a.mkv', 'tv/b.mkv'] });
    assert.strictEqual(p.toasts[0], '2 files added to the queue.');
  });

  await test('Make missing buttons say their scope; one library queues only its own files, All collections keeps the old call', async () => {
    const p = page(['libraries.js', 'library.js', 'coverage.js', 'monitor.js']);
    p.ev('renderMonitorView(' + JSON.stringify(status({ ...zero, missing: 3 }, zero)) + ')');
    assert.strictEqual(p.els['#monitor-make-subs'].textContent, 'Make missing subtitles (all collections)');
    // all collections: POST /audit like before
    p.answers['POST /audit'] = { started: true };
    p.answers['GET /state/audit?items=false'] = status(zero, zero);
    await p.els['#monitor-make-subs'].onclick();
    deep(p.calls.find(c => c.key === 'POST /audit').body, { generate: true, kinds: ['video'] });
    assert.ok(!p.calls.some(c => c.key === 'POST /audit/batch'));
    // one library: only that library's paths, through /audit/batch
    p.ev("libraries=[{id:'mv',name:'Movies',folders:['movies'],whole_folder:false}];setLibraryChoice('mv')");
    p.ev('renderMonitorView(' + JSON.stringify(status({ ...zero, missing: 3 }, zero)) + ')');
    assert.strictEqual(p.els['#monitor-make-lyrics'].textContent, 'Make missing lyrics (Movies)');
    p.answers['GET /state/audit?kind=song&paths=true&library=mv'] = { summary: {}, total: 1, paths: ['movies/x.flac'] };
    p.answers['POST /audit/batch'] = b => ({ queued: b.paths.length, errors: [] });
    p.answers['GET /state/audit?items=false&library=mv'] = status(zero, zero);
    p.toasts.length = 0;
    await p.els['#monitor-make-lyrics'].onclick();
    deep(p.calls.filter(c => c.key === 'POST /audit/batch').pop().body, { paths: ['movies/x.flac'] });
    assert.strictEqual(p.toasts[0], '1 file added to the queue. (Movies)');
  });

  await test('Library status tabs show one pane at a time and Coverage loads first', async () => {
    const p = page(['libraries.js', 'library.js', 'coverage.js', 'monitor.js']);
    let loaded = 0; p.ctx.loadCoverage = () => { loaded++; };
    p.answers['GET /state/audit?items=false'] = status(zero, zero);
    p.ev("setStatusTab('coverage')");
    deep(['coverage', 'results', 'watching'].map(n => p.els['#status-' + n].hidden), [false, true, true]);
    assert.strictEqual(loaded, 1);
    p.ev("setStatusTab('watching')");
    deep(['coverage', 'results', 'watching'].map(n => p.els['#status-' + n].hidden), [true, true, false]);
    assert.strictEqual(loaded, 1);
  });

  await test('coverage rows say what they will queue', async () => {
    const p = page(['libraries.js', 'library.js', 'coverage.js']);
    const share = (have, total) => ({ have, missing: total - have, total, percent: Math.round(1000 * have / total) / 10 });
    p.ev('coverageData=' + JSON.stringify({ videos: 33, songs: 26, targets: ['en'], checked: 1, folders: ['tv'], truncated: false,
      subtitles: [{ language: 'en', target: true, ...share(21, 33) }, { language: 'ja', target: false, ...share(33, 33) }],
      lyrics: share(19, 26), enhanced: share(26, 26) }) + ';renderCoverage()');
    const labels = el => el.children.map(r => r.children[r.children.length - 1].textContent);
    deep(labels(p.els['#coverage-subs']), ['Generate missing English subtitles (12 videos)', 'Generate missing JA subtitles (0 videos)']);
    deep(labels(p.els['#coverage-lyrics']), ['Generate missing lyrics (7 songs)', 'Generate missing enhanced lyrics (0 songs)']);
  });

  // ----- embedded subtitles and lyrics (1.5.0) -----
  const vs = (extra = {}) => ({ subs: [], missing: [], external: [], embedded: [], unknown: 0, bitmap: [], source: 'missing', checked: true, error: null, ...extra });
  const ss = (extra = {}) => ({ lyrics: false, enhanced: false, files: [], lyrics_file: false, lyrics_embedded: false, lyrics_source: 'missing', checked: true, error: null, ...extra });

  await test('chips say where subtitles and lyrics come from, and offer a retry for a failed scan', async () => {
    const p = page(['libraries.js', 'library.js']);
    const chips = (kind, status, retry) => { p.ctx.__s = status; p.ctx.__r = retry; return p.ev('statusChips(' + JSON.stringify(kind) + ',__s,__r)').children.map(c => c.textContent); };
    deep(chips('video', vs({ subs: ['en', 'es'], missing: [], external: ['es'], embedded: ['en'], source: 'both' })), ['Subs: en (embedded), es (file)', 'Embedded + file']);
    deep(chips('video', vs({ subs: ['en'], missing: ['es'], embedded: ['en'], source: 'embedded' })), ['Subs: en (embedded)', 'Embedded only', 'Missing: es']);
    deep(chips('video', vs({ subs: ['en'], missing: [], external: ['en'], embedded: ['en'], source: 'both' })), ['Subs: en (embedded + file)', 'Embedded + file']);
    deep(chips('video', vs({ subs: ['es'], missing: [], external: ['es'], source: 'external' })), ['Subs: es (file)']);
    deep(chips('video', vs({ missing: ['en'], unknown: 2, bitmap: ['ja'], embedded: ['ja'], source: 'embedded' })), ['Subs: ja (embedded)', 'Embedded only', 'Language unknown: 2 tracks', 'Image subtitles: ja', 'Missing: en']);
    deep(chips('video', { subs: ['en'], missing: [] }), ['Subs: en']);                                   // older answers keep their wording
    let retried = 0;
    deep(chips('video', vs({ subs: ['en'], external: ['en'], source: 'failed', error: 'ffprobe could not read this file' }), () => { retried++; }),
      ['Subs: en (file)', 'Scan failed', 'Retry scan']);
    deep(chips('song', ss({ lyrics: true, lyrics_embedded: true, lyrics_source: 'embedded' })), ['Lyrics: embedded', 'Enhanced: no']);
    deep(chips('song', ss({ lyrics: true, lyrics_file: true, lyrics_source: 'external' })), ['Lyrics: file', 'Enhanced: no']);
    deep(chips('song', ss({ lyrics: true, lyrics_file: true, lyrics_embedded: true, lyrics_source: 'both' })), ['Lyrics: file + embedded', 'Enhanced: no']);
    deep(chips('song', { lyrics: true, enhanced: false, files: [] }), ['Lyrics: yes', 'Enhanced: no']);
    deep(chips('song', ss()), ['Lyrics: missing']);
  });

  await test('Embedded only and Scan failed filters', async () => {
    const p = page(['libraries.js', 'library.js']);
    const inside = V('in.mkv', { status: vs({ subs: ['en'], embedded: ['en'], source: 'embedded' }) });
    const both = V('both.mkv', { status: vs({ subs: ['en'], embedded: ['en'], external: ['en'], source: 'both' }) });
    const bad = V('bad.mkv', { status: vs({ source: 'failed', error: 'x' }) });
    p.answers['GET /browse?path='] = { entries: [inside, both, bad], truncated: false };
    p.answers['GET /music/browse?path='] = { entries: [S('tag.mp3', { status: ss({ lyrics: true, lyrics_embedded: true }) }), S('file.mp3', { status: ss({ lyrics: true, lyrics_file: true, lyrics_embedded: true }) })], truncated: false };
    await p.ev('browse()');
    const run = async filter => (await p.lists.library.server({ q: '', filter, sort: 'name', offset: 0, limit: 20 })).items.map(e => e.name);
    deep(await run('v:embedded'), ['in.mkv']);
    deep(await run('s:embedded'), ['tag.mp3']);
    deep(await run('x:failed'), ['bad.mkv']);
    assert.ok(p.lists.library.filters().some(f => f[0] === 'v:embedded') && p.lists.library.filters().some(f => f[0] === 'x:failed'));
  });

  await test('files nobody has looked inside yet are checked once, in one request of at most 100; failed ones wait for Retry scan', async () => {
    const p = page(['libraries.js', 'library.js']);
    const entries = Array.from({ length: 120 }, (_, i) => V('v' + String(i).padStart(3, '0') + '.mkv', { status: vs({ checked: false, source: 'unchecked' }) }));
    entries.push(V('old.mkv'));   // older answer shape: no "checked" at all, so nothing is asked for it
    p.answers['GET /browse?path='] = { entries, truncated: false };
    p.answers['GET /music/browse?path='] = { entries: [], truncated: false };
    p.answers['POST /embedded/check'] = body => ({ statuses: Object.fromEntries(body.paths.map(path => [path, vs({ subs: ['en'], embedded: ['en'], source: 'embedded' })])) });
    await p.ev('browse()');
    const page1 = async () => p.lists.library.server({ q: '', filter: '', sort: 'name', offset: 0, limit: 200 });
    await page1(); await sleep(); await sleep();
    const asked = p.calls.filter(c => c.key === 'POST /embedded/check');
    assert.strictEqual(asked.length, 1);
    assert.strictEqual(asked[0].body.paths.length, 100);
    assert.ok(!asked[0].body.paths.includes('tv/old.mkv') && asked[0].body.force === false);
    const entriesNow = p.ev('libraryEntries');
    assert.strictEqual(entriesNow.filter(e => e.status && e.status.source === 'embedded').length, 100);
    await page1(); await sleep(); await sleep();
    const second = p.calls.filter(c => c.key === 'POST /embedded/check');
    assert.strictEqual(second.length, 2);                                   // the other 20, not the first 100 again
    assert.strictEqual(second[1].body.paths.length, 20);
    // Retry scan asks again for that one file, with force
    p.answers['POST /embedded/check'] = body => ({ statuses: { [body.paths[0]]: vs({ source: 'embedded', embedded: ['en'], subs: ['en'] }) } });
    const bad = { path: 'tv/bad.mkv', kind: 'video', status: vs({ source: 'failed', error: 'x' }) };
    p.ctx.__bad = bad;
    await p.ev('libraryRetryScan(__bad)');
    const last = p.calls[p.calls.length - 1];
    assert.ok(last.key === 'POST /embedded/check' && last.body.force === true && last.body.paths[0] === 'tv/bad.mkv');
    assert.strictEqual(bad.status.source, 'embedded');
  });

  await test('coverage says how many files were checked inside', async () => {
    const p = page(['libraries.js', 'library.js', 'coverage.js']);
    const note = inside => p.ev('insideNote(' + JSON.stringify(inside) + ')');
    assert.strictEqual(note({ checked: 40, unchecked: 8, failed: 2, total: 50, used: true }),
      'Inside files: 40 of 50 checked, 2 files could not be read (counted by their files only). Files not checked yet count by their files only; use Check inside files.');
    assert.ok(note({ checked: 1, unchecked: 0, failed: 0, total: 1, used: false }).includes('skip existing'));
    assert.strictEqual(note(undefined), '');
  });

  console.log(JSON.stringify({ passed }));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
