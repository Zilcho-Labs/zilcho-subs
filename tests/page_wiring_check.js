// Static wiring check for the page: every id a script looks up exists in index.html, the
// sidebar has the 8 entries (each with a section), and the old pages are gone.
// Usage: node tests/page_wiring_check.js <static folder>. Prints {"ids": n, "nav": [...]} or throws.
const fs = require('fs'), path = require('path'), assert = require('assert');
const STATIC = process.argv[2];
const read = f => fs.readFileSync(path.join(STATIC, f), 'utf8');
const html = read('index.html');
const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
const scripts = fs.readdirSync(STATIC).filter(f => f.endsWith('.js'));
let looked = 0;
for (const f of scripts) {
  const js = read(f);
  // ids built from a pattern ($('#'+k+'-form'), sid(k,'url')) are checked by the pages' own tests
  for (const m of js.matchAll(/\$\('#([A-Za-z][\w-]*)'\)/g)) { looked++; assert.ok(ids.has(m[1]), f + ' uses #' + m[1] + ' but index.html has no such id'); }
  for (const m of js.matchAll(/getElementById\('([^']+)'\)/g)) { looked++; assert.ok(ids.has(m[1]), f + ' looks up ' + m[1]); }
}
// Translate subtitles: target is a name-labelled dropdown fed by state.targets_by_source
assert.ok(ids.has('srt-target') && !ids.has('srt-targets'), 'srt-target dropdown replaces srt-targets');
assert.ok(/<select id="srt-target"/.test(html), 'srt-target is a select');
assert.ok(/function renderSrtTargets/.test(read('app.js')) && /targets_by_source/.test(read('app.js')), 'dropdown logic wired');
const nav = html.match(/<nav>(.*?)<\/nav>/s)[1];
const views = [...nav.matchAll(/data-view="([a-z]+)"[^>]*>([^<]*)</g)].map(m => [m[1], m[2]]);
assert.strictEqual(views.length, 8, 'sidebar has 8 entries, found ' + views.length);
const sections = [...html.matchAll(/<section id="([a-z]+)" class="view"/g)].map(m => m[1]);
for (const [v] of views) assert.ok(sections.includes(v), 'no section for ' + v);
assert.strictEqual(new Set(sections).size, sections.length, 'duplicate section ids');
for (const old of ['music', 'quality', 'coverage']) assert.ok(!sections.includes(old), old + ' is its own page again');
// no id twice (two elements with one id break $())
const all = [...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]);
assert.strictEqual(new Set(all).size, all.length, 'duplicate ids: ' + all.filter((x, i) => all.indexOf(x) !== i));
// every data-view link points at a section (old names are redirected in app.js)
for (const m of html.matchAll(/data-view="([a-z]+)"/g)) assert.ok(sections.includes(m[1]), 'link to missing page ' + m[1]);
assert.strictEqual((html.match(/<section/g) || []).length, (html.match(/<\/section>/g) || []).length);
// script order: listview, app, lyrics, libraries, library, monitor, quality, coverage, videopicker
const order = [...html.matchAll(/\/static\/([a-z]+)\.js/g)].map(m => m[1]);
assert.deepStrictEqual(order, ['listview', 'app', 'lyrics', 'libraries', 'library', 'monitor', 'quality', 'coverage', 'videopicker']);
const q = read('quality.js');
assert.ok(q.includes('Transcription confidence') && q.includes('no score') && q.includes('openEditor(job.id,f.language)'), 'Completed rows need the confidence label, no-score text and a Preview button');
console.log(JSON.stringify({ ids: looked, nav: views.map(v => v[1]) }));
