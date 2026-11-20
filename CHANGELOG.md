# Changelog

What changed in each version of Zilcho Subs, newest first. The version shown in
the app's footer comes from `zilcho_subs/__init__.py`. How and when to raise it
is in [VERSIONING.md](VERSIONING.md).

## 0.6.0 (November 19, 2026)

- New: **Dialogue cleanup** for video subtitles. With it turned on in Settings,
  Zilcho Subs removes background noise such as crowds, chanting, music and effects
  before listening, so lines buried in loud scenes are picked up. In a test on a
  loud anime scene it found 11 subtitles where there were 5, including a line the
  normal pass missed completely. Download **Dialogue cleanup** (8 MB) in Speech
  models first. It adds about a minute per episode on the CPU.
- It uses DeepFilterNet3 (MIT or Apache-2.0) on ONNX Runtime, already part of the
  app, so the image doesn't grow. It's for speech only, so song lyrics never use it
  (it would remove the singing too). Sound captions still listen to the original audio.
- Cleaned and uncleaned transcripts are cached separately, so switching it on or
  off never mixes them up, and your existing cached transcripts stay valid.

## 0.5.2 (November 18, 2026)

- Song lyrics follow the song's phrases. Lines used to be cut every 40 or so
  characters, so a line often ended with the first word of the next phrase
  ("...break my bones But" / "this feels like..."). Lines now break where a new
  phrase starts, using the singer's pauses, punctuation and the capital letter the
  speech model puts at each phrase start. A line can run up to 52 characters to
  finish a phrase, and never ends on words like "the" or "on".
- A lone "Thank you (very much)" heard long after the singing stops is no longer
  added to the end of a song.
- To fix songs done before: tick "Make lyrics again" and run them. The speech
  recognition is reused, so it only takes seconds per song.

## 0.5.1 (November 18, 2026)

- New setting: **Put the word-by-word timing in Song.lrc itself.** Some players
  (Jellyfin, which Zilcho Player uses) only read `Song.lrc`, so word-by-word karaoke
  needs the timing there. Off by default, because some older players show the word
  times as text. `Song.words.lrc` and `Song.txt` are still written as before.
- The lyrics editor shows plain text even when `Song.lrc` has word timing, and lines
  you don't change keep their word times when you save.

## 0.5.0 (November 17, 2026)

- New **Song lyrics** section: pick a song and Zilcho Subs writes synced
  lyrics next to it for music apps that show lyrics while a song plays:
  `Song.lrc` (line by line, used by Jellyfin and most players), `Song.txt`
  (plain lyrics) and `Song.words.lrc` (word by word, for karaoke-style
  players; can be turned off in Settings).
- Lyrics you already have are never overwritten: new ones go to
  `Song.AI.lrc` instead. Songs themselves are never changed.
- **Preview & edit lyrics** on a finished job to fix misheard words before
  your music apps use them.
- New setting: **Song lyrics language** (auto listens to the middle of the
  song, past any instrumental intro).

## 0.4.0 (November 17, 2026)

- Select jobs: tick finished jobs (or "Select all finished") and remove,
  archive or restore just those.
- Removing jobs no longer asks first, since it only clears the list. It still
  asks when a translated SRT exists only inside the app.
- CPU threads now take effect for every job that starts or resumes after you
  change the setting. Before, jobs kept the value from when they were queued.

## 0.3.0 (November 17, 2026)

- Tidy up the job list: finished jobs (completed, failed, skipped, cancelled)
  can be archived, restored or removed, one at a time or all at once
  ("Archive completed", "Clear failed", "Remove all archived"). Removing a job
  only takes it off the list; subtitle files and videos are never deleted.
- The About page links to zilcholabs.com.

## 0.2.1 (November 16, 2026)

- New blue, cyan and violet branding inspired by the owner's Copilot artwork, with a refreshed sign-in screen, app icon and About header.

## 0.2.0 (November 16, 2026)

- Readable subtitle timing: lines stay up long enough to read and no longer
  show text nobody said (PR #2).
- Optional sound captions such as [music] and [laughter] (PR #2).
- Word timing fixed for words that touch, and long lines split at sentence
  ends, commas or before joining words (PR #3).
- The version number now goes up with each change, and test builds are named
  after it (`0.2.0-rc.<build>`).

## 0.1.0 (November 15, 2026)

- First private test version: local subtitle generation, SRT translation,
  queue controls (pause, hold, Run next), resume after a restart and the
  Unraid template.
