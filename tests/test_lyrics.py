from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from zilcho_subs.app import create_app
from zilcho_subs.core import AUDIO, Settings, Store, media_path
from zilcho_subs.lyrics import (lines_from_lrc, lrc_time, lyric_lines, parse_lrc, process_lyrics, recognize_lyrics,
                                render_lrc, render_text)

# Two sung lines with a pause, then a long instrumental, then a third line.
WORDS = [[1.0, 1.4, ' Hello'], [1.45, 1.9, ' darkness,'], [1.95, 2.4, ' my'], [2.45, 3.0, ' friend'],
         [3.8, 4.2, " I've"], [4.25, 4.7, ' come'], [4.75, 5.4, ' again'],
         [12.0, 12.6, ' To'], [12.65, 13.4, ' talk']]


def test_lines_break_at_pauses_and_keep_word_times():
    lines = lyric_lines(WORDS)
    assert [l['text'] for l in lines] == ['Hello darkness, my friend', "I've come again", 'To talk']
    assert lines[0]['start'] == 1.0 and lines[0]['end'] == 3.0 and lines[0]['words'][1] == [1.45, 1.9, ' darkness,']
    long = [[i * 0.3, i * 0.3 + 0.25, ' word'] for i in range(30)]
    assert all(len(l['text']) <= 40 for l in lyric_lines(long))


def test_no_one_word_orphans_and_breaths_dont_split_lines():
    # Measured on a real run: "...through the summer" / "rain." and a 0.52 s breath after "Hold on tight,".
    words = [[10.08, 10.24, ' I'], [10.24, 10.48, ' keep'], [10.48, 10.78, ' on'], [10.78, 10.98, ' running'],
             [10.98, 11.26, ' through'], [11.26, 11.54, ' the'], [11.54, 11.80, ' summer'], [11.80, 12.14, ' rain.'],
             [16.30, 16.52, ' Hold'], [16.52, 16.86, ' on'], [16.86, 17.06, ' tight,'], [17.58, 17.76, ' the'],
             [17.76, 17.92, ' night'], [17.92, 18.28, ' is'], [18.28, 18.42, ' ours'], [18.42, 18.78, ' to'], [18.78, 20.08, ' stay.']]
    assert [l['text'] for l in lyric_lines(words)] == ['I keep on running through the summer rain.',
                                                       'Hold on tight, the night is ours to stay.']


def test_cjk_lines_join_without_spaces_and_count_double_width():
    words = [[i * 0.3, i * 0.3 + 0.25, c] for i, c in enumerate('夜空に光る星を見上げて今日も歌うよ君のために明日も明後日もずっと')]
    lines = lyric_lines(words)
    assert ' ' not in ''.join(l['text'] for l in lines) and len(lines) == 2 and len(lines[0]['text']) == 20


def test_lrc_line_and_word_files():
    lines = lyric_lines(WORDS)
    text = render_lrc(lines, {'title': 'Song [Live]', 'artist': 'Band', 'album': ''}, 200.5)
    assert text.splitlines()[:3] == ['[ti:Song (Live)]', '[ar:Band]', '[length:03:20]']
    assert '[00:01.00]Hello darkness, my friend' in text and "[00:03.80]I've come again" in text
    assert '[00:05.90]' in text.splitlines()  # clears the display before the instrumental
    assert text.splitlines()[-1] == '[00:13.90]'
    words = render_lrc(lines, words=True)
    assert '[00:01.00]<00:01.00>Hello <00:01.45>darkness, <00:01.95>my <00:02.45>friend<00:03.00>' in words
    cjk = render_lrc([{'start': 0, 'end': 1, 'text': '星空', 'words': [[0, .5, '星'], [.5, 1, '空']]}], words=True)
    assert '<00:00.00>星<00:00.50>空<00:01.00>' in cjk
    assert lrc_time(61.234) == '01:01.23' and lrc_time(-1) == '00:00.00'


def test_plain_text_has_verse_breaks():
    assert render_text(lyric_lines(WORDS)) == "Hello darkness, my friend\nI've come again\n\nTo talk\n"


def test_parse_lrc_accepts_edits_and_rejects_bad_input():
    parsed = parse_lrc('[ti:Song]\n[re:Zilcho Subs]\n[00:01.00]Hello darkness\n[00:03.8]Again\n[00:05.90]\n')
    assert parsed == [(1.0, 'Hello darkness'), (3.8, 'Again'), (5.9, '')]
    for bad in ['no time here', '[00:05.00]B\n[00:01.00]A', '[00:61.00]x', '[00:01.00]<b>word</b>', '[ti:Only tags]']:
        with pytest.raises(ValueError):
            parse_lrc(bad)


def test_edited_lines_keep_word_times_only_when_unchanged():
    old = lyric_lines(WORDS)
    lines = lines_from_lrc([(1.0, 'Hello darkness, my friend'), (3.8, "I've come back again"), (5.9, '')], old, 200)
    assert lines[0]['words'] == old[0]['words'] and lines[0]['end'] == 3.0
    assert 'words' not in lines[1] and lines[1]['end'] == 5.9 and len(lines) == 2


class FakeWhisper:
    """Stands in for faster-whisper: two real lines, a ♪ line and a made-up outro."""
    def __init__(self):
        self.detected = None

    def detect_language(self, audio):
        self.detected = len(audio)
        return ('en', 0.9, [])

    def transcribe(self, audio, **options):
        assert options['word_timestamps'] and not options['vad_filter'] and not options['condition_on_previous_text']
        w = lambda a, b, t: SimpleNamespace(start=a, end=b, word=t)
        segs = [SimpleNamespace(start=1, end=3, text=' ♪ Hello world ♪', words=[w(1, 1.5, ' ♪Hello'), w(1.6, 2, ' world♪')], no_speech_prob=.1, avg_logprob=-.3, compression_ratio=1.2),
                SimpleNamespace(start=4, end=6, text=' ♪♪', words=[w(4, 6, ' ♪♪')], no_speech_prob=.1, avg_logprob=-.3, compression_ratio=1.2),
                SimpleNamespace(start=7, end=9, text=' (Music)', words=[w(7, 9, ' (Music)')], no_speech_prob=.1, avg_logprob=-.3, compression_ratio=1.2),
                SimpleNamespace(start=10, end=12, text=' Thanks for watching!', words=[w(10, 12, ' Thanks')], no_speech_prob=.1, avg_logprob=-.3, compression_ratio=1.2)]
        return iter(segs), None


def test_recognize_lyrics_drops_music_symbols_and_made_up_lines():
    import numpy as np
    model = FakeWhisper()
    seen = []
    result = recognize_lyrics(model, np.zeros(16000 * 120, dtype=np.float32), 'auto', seen.append)
    assert result['language'] == 'en' and model.detected == 16000 * 30
    assert result['words'] == [[1.0, 1.5, ' Hello'], [1.6, 2.0, ' world']]
    assert seen[-1] == 12


INFO = {'duration': 30.0, 'format': {'tags': {'TITLE': 'Test Song', 'ARTIST': 'Test Band', 'ALBUM': 'Album'}},
        'streams': [{'index': 0, 'codec_type': 'audio'}]}


class FakeEngine:
    def __init__(self):
        self.calls = 0

    def lyrics(self, audio, settings, language, progress):
        self.calls += 1
        progress(15)
        return {'language': 'en', 'words': WORDS}


def extract_fake(media, index, out, duration):
    out.write_bytes(b'fake audio')


@pytest.fixture
def music(tmp_path):
    root = tmp_path / 'media'
    (root / 'music').mkdir(parents=True)
    song = root / 'music' / 'Song.flac'
    song.write_bytes(b'synthetic song')
    return Store(tmp_path / 'config'), root, song


def run(store, root, engine, **extra):
    store.enqueue('lyrics', {'path': 'music/Song.flac', 'settings': store.settings().model_dump(), **extra})
    job = store.claim()
    process_lyrics(store, job, root, engine, lambda _p: INFO, extract_fake)
    return store.job(job['id'])


def test_song_paths_are_audio_only(music):
    _store, root, song = music
    assert media_path(root, 'music/Song.flac', file=True, kinds=AUDIO) == song.resolve()
    with pytest.raises(ValueError, match='video'):
        media_path(root, 'music/Song.flac', file=True)
    (root / 'clip.mkv').write_bytes(b'video')
    with pytest.raises(ValueError, match='song'):
        media_path(root, 'clip.mkv', file=True, kinds=AUDIO)


def test_lyrics_job_writes_files_and_updates_only_its_own(music):
    store, root, song = music
    engine = FakeEngine()
    job = run(store, root, engine)
    assert job['status'] == 'completed' and job['outputs'] == {
        'lrc': 'music/Song.lrc', 'txt': 'music/Song.txt', 'words': 'music/Song.words.lrc'}
    lrc = (root / 'music' / 'Song.lrc').read_text(encoding='utf-8')
    assert '[ti:Test Song]' in lrc and '[00:01.00]Hello darkness, my friend' in lrc
    assert '<00:01.45>darkness,' in (root / 'music' / 'Song.words.lrc').read_text(encoding='utf-8')
    assert song.read_bytes() == b'synthetic song'
    # Run again without Regenerate: skipped, nothing recomputed.
    again = run(store, root, engine)
    assert again['status'] == 'skipped' and 'already made by Zilcho Subs' in again['stage'] and engine.calls == 1
    # Regenerate replaces the app's own files in place (cache reused), same names.
    again = run(store, root, engine, regenerate=True)
    assert again['outputs']['lrc'] == 'music/Song.lrc' and engine.calls == 1
    assert not list((root / 'music').glob('*.AI*'))


def test_users_own_lyrics_are_never_overwritten(music):
    store, root, _song = music
    mine = root / 'music' / 'Song.lrc'
    mine.write_text('[00:00.00]My own lyrics\n', encoding='utf-8')
    job = run(store, root, FakeEngine())
    assert job['status'] == 'skipped' and 'already has a lyrics file' in job['stage']
    job = run(store, root, FakeEngine(), regenerate=True)
    assert job['outputs']['lrc'] == 'music/Song.AI.lrc' and 'Song.AI.lrc' in job['stage']
    assert mine.read_text(encoding='utf-8') == '[00:00.00]My own lyrics\n'
    # If the user edits a file the app wrote, it becomes theirs too.
    ours = root / 'music' / 'Song.AI.lrc'
    ours.write_text('[00:00.00]Edited by hand\n', encoding='utf-8')
    job = run(store, root, FakeEngine(), regenerate=True)
    assert job['outputs']['lrc'] == 'music/Song.AI.2.lrc' and ours.read_text(encoding='utf-8') == '[00:00.00]Edited by hand\n'


def test_no_singing_and_changed_song_write_nothing(music):
    store, root, song = music

    class Silent(FakeEngine):
        def lyrics(self, *args):
            return {'language': 'en', 'words': []}

    store.enqueue('lyrics', {'path': 'music/Song.flac', 'settings': store.settings().model_dump()})
    job = store.claim()
    with pytest.raises(ValueError, match='No singing'):
        process_lyrics(store, job, root, Silent(), lambda _p: INFO, extract_fake)

    class Changing(FakeEngine):
        def lyrics(self, *args):
            song.write_bytes(b'a different song file')
            return super().lyrics(*args)

    store.update(job['id'], status='failed')
    store.enqueue('lyrics', {'path': 'music/Song.flac', 'settings': store.settings().model_dump(), 'regenerate': True})
    job = store.claim()
    with pytest.raises(ValueError, match='changed'):
        process_lyrics(store, job, root, Changing(), lambda _p: INFO, extract_fake)
    assert not list((root / 'music').glob('*.lrc'))


def test_cancel_stops_a_lyrics_job(music):
    store, root, _song = music
    store.enqueue('lyrics', {'path': 'music/Song.flac', 'settings': store.settings().model_dump()})
    job = store.claim()
    store.cancel(job['id'])
    with pytest.raises(InterruptedError):
        process_lyrics(store, job, root, FakeEngine(), lambda _p: INFO, extract_fake)


def test_settings_validate_lyrics_language():
    assert Settings(lyrics_language='ja').lyrics_language == 'ja'
    with pytest.raises(ValueError):
        Settings(lyrics_language='../en')


@pytest.fixture
def client(tmp_path):
    root = tmp_path / 'media'
    (root / 'music').mkdir(parents=True)
    (root / 'music' / 'Song.flac').write_bytes(b'0123456789' * 100)
    (root / 'music' / 'cover.jpg').write_bytes(b'jpg')
    (root / 'episode.mkv').write_bytes(b'video')
    app = create_app(tmp_path / 'config', root, 'test-password-123', run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        assert c.post('/api/login', json={'password': 'test-password-123'}).status_code == 200
        yield c


def finished(client):
    store = client.app.state.store
    root = Path(client.get('/api/state').json()['media_root'])
    id = client.post('/api/lyrics', json={'path': 'music/Song.flac'}).json()['id']
    job = store.claim()
    assert job['id'] == id and job['kind'] == 'lyrics'
    process_lyrics(store, job, root, FakeEngine(), lambda _p: INFO, extract_fake)
    return id, root


def test_music_api_browse_queue_preview_and_edit(client):
    listing = client.get('/api/music/browse?path=music').json()['entries']
    assert [e['name'] for e in listing] == ['Song.flac'] and listing[0]['lyrics'] is False
    assert client.post('/api/lyrics', json={'path': 'episode.mkv'}).status_code == 400
    assert client.post('/api/lyrics', json={'path': '../outside.flac'}).status_code == 400
    id, root = finished(client)
    assert client.get('/api/music/browse?path=music').json()['entries'][0]['lyrics'] is True
    # Playing along is for music apps; the app doesn't stream songs.
    assert client.get(f'/api/lyrics/{id}/audio').status_code == 404
    lrc = client.get(f'/api/lyrics/{id}/file/lrc').text
    assert '[00:01.00]Hello darkness, my friend' in lrc
    assert client.get(f'/api/lyrics/{id}/file/words?download=true').headers['content-disposition'].endswith('Song.words.lrc"')
    edited = lrc.replace("I've come again", "I've come to talk with you again")
    assert client.post(f'/api/lyrics/{id}', json={'text': edited}).json()['lrc'] == 'music/Song.lrc'
    assert "I've come to talk with you again" in (root / 'music' / 'Song.lrc').read_text(encoding='utf-8')
    assert "I've come to talk with you again" in (root / 'music' / 'Song.txt').read_text(encoding='utf-8')
    import json
    from zilcho_subs.lyrics import lyrics_data_path
    saved = json.loads(lyrics_data_path(client.app.state.store, id).read_text(encoding='utf-8'))
    assert saved['lines'][0]['words'] and 'words' not in saved['lines'][1]
    assert client.post(f'/api/lyrics/{id}', json={'text': 'not lyrics'}).status_code == 400
    assert client.get(f'/api/lyrics/{id}/file/bogus').status_code == 404
    other = client.post('/api/jobs', json={'path': 'episode.mkv'}).json()['id']
    assert client.get(f'/api/lyrics/{other}/file/lrc').status_code == 404
    retry = client.post(f'/api/jobs/{id}/retry', json={})
    assert retry.status_code == 200 and client.app.state.store.job(retry.json()['id'])['kind'] == 'lyrics'


def test_every_menu_item_has_its_own_section():
    import re
    html = (Path(__file__).parents[1] / 'zilcho_subs' / 'static' / 'index.html').read_text(encoding='utf-8')
    views = set(re.findall(r'<nav>.*?</nav>', html, re.S)[0].split('data-view="')[1:])
    views = {v.split('"')[0] for v in views}
    sections = re.findall(r'<section id="([a-z]+)" class="view"', html)
    assert 'music' in views and views <= set(sections) and len(sections) == len(set(sections))
    assert html.count('<section') == html.count('</section>')


def test_parse_lrc_reads_word_times_as_plain_text():
    assert parse_lrc('[00:01.00]<00:01.00>Hello <00:01.45>darkness<00:02.00>\n') == [(1.0, 'Hello darkness')]


def test_word_timing_can_go_in_song_lrc(music):
    store, root, _song = music
    store.save_settings(store.settings().model_copy(update={'lyrics_lrc_words': True}))
    job = run(store, root, FakeEngine())
    lrc = (root / 'music' / 'Song.lrc').read_text(encoding='utf-8')
    assert job['status'] == 'completed' and '[00:01.00]<00:01.00>Hello <00:01.45>darkness,' in lrc
    # The separate word file and the plain text are still written.
    assert (root / 'music' / 'Song.words.lrc').is_file()
    assert '<' not in (root / 'music' / 'Song.txt').read_text(encoding='utf-8')


def test_editing_a_word_timed_song_lrc_keeps_word_times(client):
    store = client.app.state.store
    store.save_settings(store.settings().model_copy(update={'lyrics_lrc_words': True}))
    id, root = finished(client)
    lrc = client.get(f'/api/lyrics/{id}/file/lrc').text
    assert '<00:01.45>darkness,' in lrc
    # Saving it back unchanged (word times and all) keeps every word time.
    assert client.post(f'/api/lyrics/{id}', json={'text': lrc}).status_code == 200
    assert (root / 'music' / 'Song.lrc').read_text(encoding='utf-8') == lrc
    # The editor shows plain text; an edited line loses only its own word times.
    import re
    plain = re.sub(r'<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>', '', lrc).replace("I've come again", "I've come back again")
    assert client.post(f'/api/lyrics/{id}', json={'text': plain}).status_code == 200
    saved = (root / 'music' / 'Song.lrc').read_text(encoding='utf-8')
    assert '<00:01.45>darkness,' in saved and "]I've come back again" in saved


def timed(text, start=0.0, step=0.3):
    """Evenly sung words with no pauses (the case that used to be cut every 40 characters)."""
    return [[round(start + i * step, 2), round(start + i * step + 0.28, 2), ' ' + w] for i, w in enumerate(text.split())]


def test_lines_follow_phrases_not_a_character_count():
    # The model capitalizes each phrase it hears; lines used to end on the next phrase's first word.
    words = timed('Rocks and pebbles may scratch my shoes But this one feels like a deeper cut '
                  'Broke the window when you said you would I would not look back if you let me in')
    assert [l['text'] for l in lyric_lines(words)] == [
        'Rocks and pebbles may scratch my shoes', 'But this one feels like a deeper cut',
        'Broke the window when you said you would', 'I would not look back if you let me in']


def test_names_mid_line_and_lines_a_bit_long_stay_together():
    words = timed('Another long night, driving miles on the Chevy '
                  'And I would be lying if I said it does not move me Can anybody hear me')
    assert [l['text'] for l in lyric_lines(words)] == [
        'Another long night, driving miles on the Chevy',
        'And I would be lying if I said it does not move me', 'Can anybody hear me']


def test_thank_you_after_the_song_ends_is_dropped():
    words = timed('We were young and never afraid') + timed('Thank you very much.', start=40)
    assert [l['text'] for l in lyric_lines(words)] == ['We were young and never afraid']
    sung = timed('We were young and never afraid') + timed('Thank you', start=2.5)
    assert lyric_lines(sung)[-1]['text'].endswith('Thank you')  # sung, so kept
