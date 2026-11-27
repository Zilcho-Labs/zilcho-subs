# Changelog

What changed in each version of Zilcho Subs, newest first. The version shown in
the app's footer comes from `zilcho_subs/__init__.py`. How and when to raise it
is in [VERSIONING.md](VERSIONING.md).

## 0.16.1 (November 27, 2026)

- **New icon:** the teal ZL icon with the glowing border.

## 0.16.0 (November 26, 2026)

- **API key for other apps.** In Settings you can create a key that lets Zilcho
  Forge ask for subtitles and follow their progress. It can do nothing else, is
  shown only once, and can be replaced or removed.

## 0.15.1 (November 26, 2026)

- **New look.** The new metal ZL icon replaces the old film-strip logo, and
  highlights are now teal to match it.

## 0.15.0 (November 22, 2026)

- **Translate subtitles: Browse for the video.** **Attach to a video
  (optional)** now has **Browse…** and **Clear** buttons. Browse opens a
  picker that starts beside the chosen SRT, lists folders and videos from your
  collections, and fills the path when you click a video. Typing the path still
  works, and leaving it empty queues the translation without a video. The
  server now also refuses a video that is outside every collection.

- **Libraries are now called Collections.** The section in Settings is
  **Collections**, and the selector on Library, Library status and Queue >
  Completed is labelled **Collection** with **All collections** plus your saved
  names. The page names **Library** and **Library status** stay, and so do the
  All / Videos / Songs buttons. Saved names, folders, the remembered choice and
  the **Everything (main folder)** collection are unchanged. Only wording
  changed; the API (`/api/libraries`, `?library=`) works as before.

- **Translate subtitles: pick the target by name.** The target-code box is now
  a **Target language** dropdown (for example "Spanish (es)") listing every
  language reachable from the original language through your installed
  Language packs, including non-English ones and chained routes. It refreshes
  when you change the original language or finish installing a pack. With no
  choices it says so and points to **Language packs**. The server also checks
  that the route exists. One target per submit.

- **Embedded subtitles and lyrics are detected.** Library and Library status
  now look inside video files for subtitle tracks (including MKV) and inside
  songs for lyrics tags (mp3, flac, m4a, ogg, opus, wma). Chips show where each
  language comes from, for example **Subs: en (embedded), es (file)**, plus
  **Embedded only**, **Embedded + file**, **Lyrics: embedded** and **Scan
  failed** with **Retry scan**. Files are looked at only when needed (the files
  on screen, changed files during scanning, or the new **Check inside files**
  button in Coverage), and the answers are remembered, so opening a page never
  scans the whole collection. Coverage and **Generate missing** count a
  subtitle track as present only when **Skip existing** is on; untagged tracks
  show **Language unknown** and never count; embedded lyrics count as lyrics
  but not as enhanced lyrics; a file that could not be read is counted by its
  files alone. Nothing in your media is changed.

- **Known limits.** Picture-based subtitle tracks (such as PGS) are shown but
  are not text and are labelled as image subtitles. Embedded lyrics are never
  counted as word-by-word lyrics.

## 0.14.0 (November 22, 2026)

- **Confidence bar on finished jobs.** In Queue > Completed the column is now
  **Transcription confidence**: a percentage with a bar that shades red to green.
  It only shows how sure the speech model was; translated languages reuse that
  number and it is not a translation-accuracy score. Jobs made before 0.10.0 and
  SRT-only translations show **no score**. The bands are unchanged (Excellent 95+,
  Good 90-95, Fair 85-90, Poor under 85). Every finished file has a **Preview**
  button that opens that language's subtitles.

## 0.13.0 (November 22, 2026)

A new sidebar: 11 entries became 8. Nothing was removed; features moved.

- **One Library page.** Media library and Song lyrics are now one **Library**
  page: a library dropdown (remembered between visits), a search box that
  searches file names across the whole chosen library, the folder browser, and
  All / Videos / Songs buttons. Every row has its own button (Make subtitles for
  a video, Make lyrics for a song). The audio stream box and the two
  Regenerate boxes are in an **Options** fold. You can tick videos and songs
  together; they are queued as the right kind of job.
- **Library status.** Monitor and Coverage are now one page, **Library status**,
  with a switch for **Coverage** (opens first), **File results** and
  **Watching**. The library you pick carries across the whole page.
- **Buttons where you need them.** Each coverage row now says, for example,
  "Generate missing English subtitles (12 videos)". **Retry N failed files**
  sits next to the Show filter in File results and is hidden when nothing
  failed. "Make missing subtitles" and "Make missing lyrics" follow the
  library you picked (all libraries when you picked All libraries).
- **Settings** now holds the Libraries manager and the Media servers panel.
  **Subtitle quality** moved to **Queue > Completed**.
- New read-only `GET /api/library/search` (file-name search over a library).

## 0.12.0 (November 20, 2026)

- **Pick library folders by browsing.** In the Libraries panel on the Monitor
  page, click **+ Add folder** and click through your media folders in a
  popup. When you are inside the one you want, click **Add this folder**
  (everything inside it is included). Click **+ Add another folder** to add
  more, and the x on a folder to remove it. No more typing folder names.
- **No duplicate "Library" in the dropdowns.** The library made for you on
  upgrade (your whole main folder) is now called "Everything (main folder)" in
  the Libraries panel and no longer appears in the Library dropdowns, because
  "All libraries" already means the same. Delete it once your own libraries
  are set up and "All libraries" shows just those.

## 0.10.0 (November 20, 2026)

- **Named libraries (behind the scenes).** The folders you watch are now
  grouped into named libraries (Movies, TV, Music...), each with one or more
  folders; one can be marked "whole folder" for your main media folder. Your
  current folders become one library automatically. Files outside every
  library no longer show up on any list, search, Monitor, Coverage or Quality
  page, and those pages can look at one library with `?library=`. The
  libraries screen itself comes next.
- **Libraries screen and library picker.** On the Monitor page, make, rename
  and delete libraries (a name plus its folders, or "use my whole main folder",
  which warns that searching it scans the entire folder). Deleting a library
  asks first and never deletes files. Media library, Song lyrics, Monitor,
  Coverage and Subtitle quality each have a Library dropdown ("All libraries"
  by default) that remembers your last choice. The old "Folders to watch" box
  is replaced by this screen.
- **New Subtitle quality page.** Every subtitle file a finished job wrote, with
  its word count, segments (subtitle lines), how sure the speech model was
  ("Confidence: 98.1%") and a rating: Excellent (95% and up), Good (90–95%),
  Fair (85–90%) or Poor (under 85%). Filter by rating to find the files worth
  checking first.
- Confidence is recorded from now on while a job listens to speech; the
  subtitles themselves come out exactly as before. Files made before 0.10.0
  and translated SRT uploads show words and segments only ("not recorded").
- Editing a subtitle updates its counts and keeps its confidence.
- **New Coverage page.** See how much of your library has subtitles in each
  language ("English Subs 97%", "Spanish Subs 64%"), lyrics, and enhanced
  word-by-word lyrics, with a bar for each. One click on "Generate Missing
  Spanish" (or Lyrics, or Enhanced Lyrics) queues a normal job for every file
  that lacks it. Files already in the queue aren't added twice, and existing
  subtitle and lyric files are never replaced.
- **Emby and Plex links, next to Jellyfin.** On the Monitor page, under
  Media servers (optional), you can now also connect Emby or Plex. When new
  subtitles or lyrics are made, Zilcho Subs asks the server to look at just
  that folder, so they show up straight away. Each one is off until you fill
  it in, and the key or token never leaves Zilcho Subs.

## 0.11.0 (November 20, 2026)

- Release of the merged feature set: the list pages overhaul (0.9.0), the
  subtitle quality and coverage pages (0.10.0), and named libraries
  (0.10.0). Every page now defaults to "All libraries" and only shows the
  files from the library you choose.
- Test builds are named `0.11.0-rc.<GitHub run number>`.

## 0.9.0 (November 20, 2026)

- **See what each file has at a glance.** Media library rows show the
  subtitle languages next to each video ("Subs: en, es") and which of your
  languages are missing, or "Subs: missing". Song lyrics and Monitor library
  rows show "Lyrics: yes" and "Enhanced: yes" (word-by-word lyrics, in
  Song.words.lrc or inside Song.lrc), or "Lyrics: missing".
- **Search as you type** on Media library, Song lyrics and Monitor library:
  type `C` to see Cat, Core and Clip, `Co` to see just Core. Best matches come
  first. Media library and Song lyrics search the folder you're in; Monitor
  library searches the whole checked library.
- **Filters:** show only videos missing a chosen subtitle language, or songs
  missing lyrics (or word-by-word lyrics). **Sort** by name or by what's missing.
- **Pages:** 20 results a page with page numbers, Next page and Last page. The
  gear button next to the filter sets how many per page (0 = everything on one
  page); each page remembers its own choice in this browser.
- **Select and queue many files at once:** tick the files you want (or Select
  page / Select all), then **Queue selected**. Media library and Song lyrics also
  have **Queue all** for everything shown. These make the same jobs as the
  per-file buttons, so nothing already there is replaced.
- **Manage many queue jobs at once:** every job on the Queue page has a tick
  box, including waiting and running ones. Select all, Select waiting &
  running, Select finished and Clear selection sit above the list (all
  always shown; greyed out when they would do nothing). Click "3 selected"
  to list only the ticked jobs. Then **Cancel selected**, **Remove selected**
  or **Archive selected**. A message
  says how many changed ("3 jobs cancelled."). Cancelling more than one asks
  first.
- Alternating blue rows, a highlighted row for selected files, and a proper
  **Up one folder** button. Folders with up to 5,000 items are listed (was 1,000).

## 0.8.0 (November 19, 2026)

- New: **optional Jellyfin link** on the Monitor library page. Enter your
  Jellyfin address and an API key, and Zilcho Subs tells Jellyfin which folder
  has new subtitles or lyrics as soon as a job finishes, so they show up without
  waiting for Jellyfin's own scan. You can also have it ask Jellyfin to rescan
  its libraries after a full check. Leave it empty if you don't use Jellyfin;
  nothing is sent anywhere. **Test connection** checks the address and key, and
  the key is never shown again after saving.
- The status card in the sidebar now always shows how many items are missing,
  how many jobs are waiting in the queue and when the library was last checked,
  and warns if Jellyfin can't be reached. The browser tab shows a count of
  finished results you haven't dismissed yet.

## 0.7.0 (November 19, 2026)

- New: **Monitor library**. A new page checks your media folder for videos
  without subtitles and songs without lyrics, lists what it found, and queues the
  missing ones with one click (**Make missing subtitles**, **Make missing
  lyrics**) or automatically while watching is on. Songs are only done
  automatically if you tick that option.
- A status card in the sidebar shows what the monitor is doing ("Watching…",
  "Paused", "3 items generated, 1 failed") on every page, and keeps the results
  until you dismiss them.
- It never changes, replaces or re-makes a file that's already there, waits for
  new files to finish copying, and uses the normal queue, so still only one job
  runs at a time. It now keeps checking while a long job runs.
- Library watching, processing hours and "skip existing subtitles" moved from
  Settings to the Monitor library page. New installs check every 60 seconds.

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
