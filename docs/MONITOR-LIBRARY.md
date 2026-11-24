# Library status (the monitor)

The **Library status** page has three views: **Coverage** (opens first: how
much of the library you picked has subtitles in each language and lyrics, with a
"Generate missing ..." button on each row that says how many files it will
queue), **File results** (what the last check found, Scan now, the Make
buttons and a **Retry N failed files** button beside the Show filter, hidden
when nothing failed) and **Watching** (start or stop watching, and the
processing hours and other settings). The library dropdown at the top applies to
all three, and to **Library** and **Queue > Completed** too; it is remembered
between visits.

The monitor checks your media folder for videos that have no subtitles and
songs that have no lyrics. It lists what it found and queues the missing ones,
either when you press a button or by itself while watching is on. **Make
missing subtitles** and **Make missing lyrics** follow the collection you picked
(every collection when it says "all collections"); **Scan now** only reads, and
always looks at every collection.

Everything happens on your own machine. Nothing is uploaded.

## Setup

1. **Map your media folder** in Docker or Unraid. On Unraid it's usually
   `/mnt/user/data/media` on the server, which the app sees as `/media`. The
   monitor can only see what's inside that mapping.
2. **Download a speech model** in *Speech models* (the one chosen in Settings).
3. Open **Settings** and, under *Collections*, add a collection for each group of
   folders to watch (for example TV, Movies, Music). Folders are chosen by
   browsing the media folder.
4. Open **Library status → File results** and press **Scan now**. It only
   looks; nothing is queued yet. Check the list.
5. Press **Make missing subtitles** (and **Make missing lyrics** for songs), or
   tick **Watch the library** so new files are handled by themselves.

## Settings

These are on the **Library status → Watching** view (the folders are the
collections in **Settings**).

| Setting | Default | What it does |
|---|---|---|
| Watch the library (`watcher_enabled`) | Off | Checks on a timer and queues videos that are missing subtitles. The first check includes files that are already there. |
| Also make lyrics automatically (`auto_lyrics`) | Off | While watching, also queues lyrics for songs that have none. Off by default because music libraries are big. **Make missing lyrics** works either way. |
| Skip languages that already have a subtitle (`skip_existing`) | On | Any subtitle next to the video (`.srt`, `.ass`, `.ssa`, `.vtt`, `.sub`, not forced and not empty) or a track inside the video counts. Turned off, only Zilcho Subs' own `.AI` subtitles count. Your own files are never touched either way. Applies to every job. |
| Folders to watch (`scan_dirs`) | whole media folder | Relative folders inside the media folder. |
| Check every (`scan_interval`) | 60 seconds | How often it checks while watching. Minimum 30. |
| Wait for files to settle (`stable_seconds`) | 120 seconds | A new or changed file is left alone until its size and date stop changing for this long, so copies and downloads finish first. Files last changed longer ago than this (most of an existing library) count as settled straight away. |
| Processing hours (`schedule_start`, `schedule_end`) | all day | Jobs only *start* between these hours (container time zone). Outside them, the monitor still checks and queues, and the jobs wait. Same hour twice means all day. Applies to every job. |

Settings changes that change the result (model, languages, lyrics options and
so on) let each file be tried once more with the new settings.

## What counts as "already has it"

**Videos** (`.mkv .mp4 .m4v .avi .mov .webm .ts`), for each subtitle language in Settings:

- `Show S01E01.en.AI.srt` or a later version like `Show S01E01.en.AI.2.srt` (made by Zilcho Subs);
- with *Skip languages…* on, also your own `Show S01E01.en.srt`, `.en.ass`, `.en.vtt` and so on;
- a subtitle track inside the file. The quick check only reads file names, so
  this is found when the job runs: the job ends as *skipped* and the file is
  marked done, never queued again.

**Songs** (`.mp3 .flac .m4a .ogg .opus .wav` and the other audio types): any of
`Song.lrc`, `Song.txt`, `Song.words.lrc`, or Zilcho Subs' `Song.AI.lrc`.

## How it works

- A background thread checks the folders every *Check every* seconds while
  watching is on. It's separate from the job worker, so it keeps checking while
  a long job runs. **Scan now** and the **Make** buttons run a full check right away.
- Each check reads only file names, sizes and dates. It doesn't open the media.
  Big libraries are checked 5,000 files at a time; the next check carries on
  where the last one stopped.
- What it found is saved in the app data (`queue.sqlite3`, tables `audit` and
  `monitor_state`, plus `monitor-cursor.json`). Nothing is written to the
  media folder by the check itself, so it works with a read-only container.
- Missing files are queued as ordinary jobs in the normal queue, exactly as if
  you'd queued them from *Library*. One job runs at a
  time; you can pause, reorder, cancel and retry them as usual.
- The monitor never sets *Regenerate*, so a job it queues can't replace or add a
  new version of a file that's already there. The job checks again just before
  writing, and subtitle and lyric files are only ever created, never overwritten.
- A file is queued once per settings combination. If its job **fails**, it's
  marked *failed* and isn't retried by itself (so a broken file can't loop).
  Press **Make missing subtitles**/**lyrics**, or **Make subtitles** on that
  row, to try again.
- Files that disappear (or folders you stop watching) drop off the list after
  the next full check.

## The status card

The card at the bottom of the sidebar is visible on every page. It's the app's
"tray icon": you don't have to open Library status to know what's going on.

The top line is the monitor's state:

| It says | Meaning |
|---|---|
| Watching… | Watching is on and nothing new has finished since you last dismissed. |
| Scanning your library… | A check is running. |
| 3 items generated, 1 failed | Jobs the monitor queued have finished since you last dismissed. Stays until you press ×. |
| Paused | Watching is on but the queue is paused (the *Pause queue* button). |
| Waiting for processing hours | Watching is on, outside the processing hours. |
| Monitor off | Watching is off. Buttons still work. |

The second line always shows, when they apply: how many files are **missing**
subtitles or lyrics (including failed ones), how many **jobs are waiting in the
queue** (all jobs, not only the monitor's), **when it last checked**, and
**Jellyfin not reachable** if the Jellyfin link is on and its last call failed.
Hover over the card to see when the last *full* check finished.

While there are results you haven't dismissed, the browser tab's title starts
with their number, for example `(4) Zilcho Subs`.

Click the card to open Library status. A red edge means something failed.

The numbers live in the app data (`monitor_state` in `queue.sqlite3`), so they
survive a restart. The page asks for them every 3 seconds while it's open.

## Jellyfin (optional)

You don't need Jellyfin. With the Jellyfin fields empty (the default), nothing
is sent anywhere and everything else works the same. Jellyfin still finds the
new files on its own scheduled library scan.

If you set it up, Zilcho Subs tells Jellyfin about new files straight away.

### Setup

1. In Jellyfin, open **Dashboard → API Keys** and add a key named `Zilcho Subs`.
2. In Zilcho Subs, open **Settings → Media servers → Jellyfin** and fill in:
   - **Jellyfin address**: how this container reaches Jellyfin, for example
     `http://192.168.1.10:8096` (the server's address and Jellyfin's port).
   - **API key**: the key from step 1.
   - **Media folder as Jellyfin sees it**: the same media folder, as it's mapped
     in the *Jellyfin* container. If both containers map
     `/mnt/user/data/media` to `/media`, leave `/media`. If Jellyfin maps it to
     `/data/media`, enter `/data/media`.
3. Press **Save Jellyfin link**, then **Test connection**. It should say
   *Connected to <your server name> <version>*.

### What it does

| Option | Default | What it sends |
|---|---|---|
| Tell Jellyfin when new subtitles or lyrics are made (`notify`) | On | When a subtitle or lyrics job finishes and wrote a file into the library, `POST /Library/Media/Updated` with that file's folder (as Jellyfin sees it). Jellyfin refreshes just that folder, so the new subtitle track or lyrics appear within a minute. Folders are sent together, up to 200 at a time, checked every few seconds. |
| Also ask Jellyfin to rescan all its libraries after a full check (`refresh_after_audit`) | Off | `POST /Library/Refresh` after a full Monitor check finishes: every time you press **Scan now** or a **Make** button, and at most once an hour while watching. This is heavier on big libraries; most people only need the first option. |

Not sent: jobs that were skipped or failed, and subtitles from *SRT
translation* that were saved in the app data instead of the library.

### Safety

- **The key stays on the server.** It's kept in the app data in `jellyfin.json`
  (owner-only permissions where the file system allows it), is never sent back
  to the web page (the page only learns *whether* a key is saved), and is never
  written to a log. Saving the form with the key field empty keeps the saved key.
  **Turn off and forget the key** deletes the file.
- Only the address you entered is contacted. Redirects aren't followed, so the
  key can't be passed on to another address. Addresses with a user name or
  password in them are refused; use `http://` or `https://` with the port.
- Each call has an 8-second time limit and runs in the monitor's own thread. If
  Jellyfin is down, the error shows on the card and in the Jellyfin panel, the
  monitor and the queue carry on, and it tries again after 2 minutes. Nothing is
  lost: it picks up from the last job it announced.
- When the link is first turned on it doesn't announce old jobs; it starts from
  that moment. While **Tell Jellyfin** is off, nothing piles up for later.
- No media file is read or changed by the Jellyfin link.

### Troubleshooting

| Message | Fix |
|---|---|
| Can't reach Jellyfin at … | Check the address and port from inside the container's network. On Unraid, use the server's IP rather than `localhost`. |
| Jellyfin refused the API key | Make a new key in Jellyfin (Dashboard → API Keys) and paste it again. |
| Jellyfin answered with a redirect | Enter the exact address, for example `https://` instead of `http://`, or add the port. |
| Connected, but new subtitles don't show | The *Media folder as Jellyfin sees it* doesn't match Jellyfin's mapping, so Jellyfin doesn't recognise the folder. Check the path in Jellyfin's library settings. |

## Emby and Plex (optional)

They work the same way as the Jellyfin link, and each is set up on its own on
in **Settings** under **Media servers (optional)**. Use any of the three, all
of them, or none. Left empty (the default), nothing is sent anywhere. The
subtitle and lyric files are written next to your media either way; these links
only ask the server to look at that folder now instead of at its next
scheduled scan. Any other new files in that folder (cover art, for example)
are picked up by the same scan.

### Emby

1. In Emby, open **Settings → Advanced → API Keys** and add a key named `Zilcho Subs`.
2. Fill in **Emby address** (for example `http://192.168.1.10:8096`), the key,
   and **Media folder as Emby sees it**, then **Save Emby link** and **Test connection**.

It sends `POST /emby/Library/Media/Updated` with the changed folders (up to 200
at a time) and, with the rescan option, `POST /emby/Library/Refresh`. The key
goes in the `X-Emby-Token` header and is kept in `emby.json`.

### Plex

1. Find your Plex token: in Plex Web open any movie or episode → **⋯ → Get Info
   → View XML**. The address that opens ends in `X-Plex-Token=…`; copy that value.
2. Fill in **Plex address** (for example `http://192.168.1.10:32400`), the
   token, and **Media folder as Plex sees it** (how the Plex container sees the
   same media folder, for example `/data` or `/media`), then **Save Plex link**
   and **Test connection**.

Plex has no "this folder changed" call, so Zilcho Subs asks Plex which library
each folder belongs to (`GET /library/sections`) and runs a **partial scan of
just that folder** (`GET /library/sections/<id>/refresh?path=<folder>`), the
same scan Plex runs itself when it notices a change. If more than 25 folders in
one library changed at once, that library is scanned once instead. With the
rescan option it also asks for `/library/sections/all/refresh` after a full
Monitor check. The token goes in the `X-Plex-Token` header (never in the
address) and is kept in `plex.json`.

If a new file's folder isn't inside any Plex library folder, nothing is sent
and the Plex panel says **Check the folder**: the *Media folder as Plex sees
it* doesn't match the paths in Plex's library settings. Plex shows lyrics from
`Song.lrc` / `Song.txt` when the music library uses local media assets
(Plex's default for music libraries).

The safety rules are the same as for Jellyfin: the key or token never reaches
the web page or a log, redirects aren't followed, 8-second time limit, a server
that's down is retried after 2 minutes without losing anything, and one server
being down never stops the others or the monitor.

## API

All need a signed-in session, like the rest of the API.

- `POST /api/audit` with `{"generate": false}`: full check (in the background
  when the app is running; the result shows up in the state).
  `{"generate": true, "kinds": ["video"]}` or `["song"]` also queues what's missing.
  Add `"path": "tv/Show/S01E01.mkv"` to check (and queue) one file now.
- `POST /api/audit/dismiss`: clears the "generated / failed" counts on the card.
- `POST /api/audit/batch` with `{"paths": [...]}` (up to 5,000): Queue selected.
  The same as each file's own Make button; files that already have everything
  or are already queued are left alone. Returns `queued` and per-file `errors`.
- `GET /api/state/audit?state=missing&kind=video&limit=200&offset=0`: the summary
  plus a page of files. `items=false` returns the summary only. `limit=0` means
  everything (up to 10,000). `q=` searches file names as you type (fuzzy; best
  matches first, folder names count too). `missing=en` (a language code) or
  `missing=lyrics` keeps files still missing that. `sort=status|name|name_desc`.
  `paths=true` returns just the paths that can be queued (missing or failed),
  for Select all. Each item has a `status`: `{"subs": [...], "missing": [...]}`
  for videos, `{"lyrics", "enhanced", "files"}` for songs.
- Library page: `GET /api/browse` (videos and `.srt` files) and `GET /api/music/browse`
  (songs) are merged by the page; their entries have the same `status`.
  `GET /api/library/search?q=&kind=video|song&library=&limit=&offset=` searches
  file names across the whole library (read-only; best match first, same fuzzy
  rules as above; up to 100,000 files walked, name list kept for 60 seconds;
  unknown `library` is 404) and returns the same entries with `kind` and `folder`.
  `limit` is at most 1,000; `POST /api/jobs/batch` with `{"paths",
  "audio_stream", "regenerate"}` and `POST /api/lyrics/batch` with `{"paths",
  "regenerate"}` queue many files (one normal job each).
  The summary includes `status`, `missing`, `pending_jobs`, `in_queue`,
  `last_scan`, `last_full_scan`, `generated`, `failed` and a short `jellyfin`
  block (`enabled`, `error`, `last_ok`, `last_notified`).
- `GET /api/jellyfin`: the Jellyfin link settings and its last result
  (`has_key` instead of the key).
- `POST /api/jellyfin` with `{"url", "api_key", "media_root", "notify",
  "refresh_after_audit"}`: saves it. An empty `api_key` keeps the saved key; an
  empty `url` turns the link off.
- `POST /api/jellyfin/test` with `{}` tests what's saved, or with
  `{"url", "api_key"}` tests those first (the empty key means the saved one).
- `POST /api/jellyfin/forget`: turns the link off and deletes the saved key.
- `GET /api/servers`: all three links (`jellyfin`, `emby`, `plex`) in the same
  shape as `GET /api/jellyfin`, plus `kind` and `name`.
- `GET|POST /api/servers/<kind>`, `POST /api/servers/<kind>/test` and
  `POST /api/servers/<kind>/forget` work like the Jellyfin ones, for `kind` =
  `jellyfin`, `emby` or `plex` (the key field is the Plex token for Plex).
  The status summary also has `servers`: one short block per server.

## Testing

`tests/test_monitor.py` uses synthetic files and fake speech recognition only.
It covers what counts as present, the settle wait, queue-once and
no-regeneration rules, failed jobs, removed files, big libraries in slices,
upgrading from the old watcher, the status card counts, the API, an end-to-end
run that checks no existing file changes, and a smoke test of the background thread.

`tests/test_jellyfin.py` runs a fake Jellyfin server on `127.0.0.1` inside the
test (nothing else is contacted). It covers: off by default (no requests), the
folders and paths sent after finished jobs, only once each, Jellyfin down
(error shown, retried later, nothing lost, the monitor keeps checking), a wrong
key, redirects not followed, the hourly rescan limit, the key never appearing in
any API answer, the status card counts, and the background thread end to end.
