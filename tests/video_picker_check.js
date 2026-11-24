// Runs static/videopicker.js against a stub page and a stub api.
// Usage: node tests/video_picker_check.js <static folder>. Prints {"passed": [...]} or throws.
const vm = require('vm'), fs = require('fs'), path = require('path'), assert = require('assert');
const STATIC = process.argv[2];
const passed = [];
const test = async (name, fn) => { await fn(); passed.push(name); };
const deep = (a, b, m) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b), m);

function fakeEl() {
  return { value: '', textContent: '', disabled: false, className: '', children: [], opened: false, onclick: null,
    append(...a) { this.children.push(...a); }, replaceChildren(...a) { this.children = a; },
    showModal() { this.opened = true; }, close() { this.opened = false; } };
}
function page() {
  const els = {}, calls = [], toasts = [], answers = {};
  const ctx = vm.createContext({ console, encodeURIComponent, JSON, Promise, Error });
  ctx.$ = s => els[s] || (els[s] = fakeEl());
  ctx.node = (tag, text, cls) => { const e = fakeEl(); e.tag = tag; if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  ctx.toast = t => toasts.push(t);
  ctx.withLibrary = p => p;
  ctx.api = async p => { calls.push(p); const a = answers[p]; if (a === undefined) throw Error('That folder is not in a collection'); return a; };
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'videopicker.js'), 'utf8'), ctx, { filename: 'videopicker.js' });
  vm.runInContext('initVideoPicker()', ctx);
  return { ctx, els, calls, toasts, answers, el: s => ctx.$(s) };
}
const dir = (name, p) => ({ path: p, name, directory: true, subtitle: false });
const vid = (name, p) => ({ path: p, name, directory: false, subtitle: false });
const srt = (name, p) => ({ path: p, name, directory: false, subtitle: true });
const sleep = () => new Promise(r => setImmediate(r));
const listed = p => p.el('#video-list').children.map(c => c.children && c.children.length ? c.children[0].textContent : c.textContent);

(async () => {
  await test('opens beside the chosen SRT, hides .srt entries, picking a video fills the box', async () => {
    const p = page();
    p.el('#srt-path').value = 'tv/Show/A.en.srt';
    p.answers['/browse?path=tv%2FShow'] = { path: 'tv/Show', entries: [vid('A.mkv', 'tv/Show/A.mkv'), srt('A.en.srt', 'tv/Show/A.en.srt')], truncated: false };
    await p.el('#srt-video-browse').onclick();
    assert.ok(p.el('#video-picker').opened);
    deep(listed(p), ['A.mkv']);
    p.el('#video-list').children[0].children[0].onclick();
    assert.strictEqual(p.el('#srt-video').value, 'tv/Show/A.mkv');
    assert.ok(!p.el('#video-picker').opened);
  });
  await test('falls back to the top when the SRT folder is unavailable; enter a folder, then Up', async () => {
    const p = page();
    p.el('#srt-path').value = 'gone/x.srt';
    p.answers['/browse?path='] = { path: '', entries: [dir('tv', 'tv')], truncated: false };
    p.answers['/browse?path=tv'] = { path: 'tv', entries: [vid('B.mkv', 'tv/B.mkv')], truncated: false };
    await p.el('#srt-video-browse').onclick();
    assert.ok(p.el('#video-up').disabled);
    p.el('#video-list').children[0].children[0].onclick(); await sleep();
    assert.strictEqual(p.el('#video-current').textContent, '/tv'); assert.ok(!p.el('#video-up').disabled);
    p.el('#video-up').onclick(); await sleep();
    assert.ok(p.el('#video-up').disabled);
  });
  await test('empty folder shows the empty text; Cancel leaves the box alone', async () => {
    const p = page();
    p.el('#srt-video').value = 'typed/by/hand.mkv';
    p.answers['/browse?path='] = { path: '', entries: [], truncated: false };
    await p.el('#srt-video-browse').onclick();
    deep(listed(p), ['No videos in this folder.']);
    p.el('#video-picker').close();   // Cancel / Esc just close the dialog
    assert.strictEqual(p.el('#srt-video').value, 'typed/by/hand.mkv');
    p.el('#srt-video-clear').onclick(); assert.strictEqual(p.el('#srt-video').value, '');
  });
  await test('a browse error is toasted and the last good folder stays', async () => {
    const p = page();
    p.answers['/browse?path='] = { path: '', entries: [dir('bad', 'bad'), vid('a.mkv', 'a.mkv')], truncated: true };
    await p.el('#srt-video-browse').onclick();
    p.el('#video-list').children[0].children[0].onclick(); await sleep();
    assert.ok(p.toasts.includes('That folder is not in a collection'));
    assert.strictEqual(p.el('#video-current').textContent, '/ (main media folder)');
    assert.strictEqual(listed(p).length, 3);   // folder, video, truncation note
  });
  console.log(JSON.stringify({ passed }));
})().catch(e => { console.error(e); process.exit(1); });
