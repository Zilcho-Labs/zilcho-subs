# Zilcho Subs

**Local AI subtitles. Your media, your language.**

Zilcho Subs is a self-hosted web app that makes subtitle and lyric files for your own media library, entirely on your own machine. It runs on the CPU in Docker (Unraid template included) and writes standard files next to your media, which Jellyfin and most players pick up automatically. Nothing is uploaded to a cloud service, and there's no paid API.

Made by [Zilcho Labs](https://zilcholabs.com). Zilcho Subs is an independent project and isn't affiliated with Jellyfin, OpenAI, SYSTRAN or Argos Translate.

## What it does

- **Subtitles from speech.** Listens to a video's audio with a local Whisper model (via faster-whisper) and writes a selectable `.srt` track. Each line is timed from the words actually heard, and stays on screen long enough to read.
- **Translation.** English output can come straight from the speech model. Other languages use local, offline Argos-format translation packs. Japanese, Korean and Chinese audio were the first focus. Other languages depend on the speech model and the packs you install.
- **Translate existing subtitles.** Upload or pick an `.srt` file and translate it. No speech model is needed, and the original timing is kept exactly.
- **Sound captions (optional).** Adds captions such as `[Dog barking]` to English subtitles using a small local sound classifier.
- **Song lyrics.** Makes `.lrc` lyric files for music, line by line and optionally word by word for karaoke-style players. Lines follow the song's phrases.
- **A queue that's easy to manage.** Pause, reorder, retry and "run next"; an interrupted video resumes from its last finished part. You can also set processing hours and watch library folders automatically.
- **Your files are never changed.** Videos, songs and subtitle or lyric files that you (or another app) made are never modified or overwritten. New subtitles are always new files; regenerating makes a new version. Lyric files that Zilcho Subs made itself are updated in place when you make them again, unless you've edited them, in which case they're yours and left alone.

## Install

You need a Linux x86-64 Docker host with enough memory for the speech model you pick (the default container limit is 2 CPUs and 6 GB). Speech models and translation packs are downloaded only when you ask for them in the app.

### Unraid

1. Copy `unraid/zilcho-subs.xml` to `/boot/config/plugins/dockerMan/templates-user/my-zilcho-subs.xml`.
2. In Unraid, open **Docker → Add Container** and choose the Zilcho Subs template.
3. Set these, then click **Apply**:
   - **Media Library:** your final, imported media folder.
   - **App Data:** for example `/mnt/user/appdata/zilcho-subs`.
   - **Web UI Password:** at least 12 characters.
   - **Port**, **PUID/PGID** (to match your media permissions) and **TZ**.

Unraid shows shares as `\\SERVER\share` on Windows. In Docker they're host paths such as `/mnt/user/...`, so check the real path in Unraid before mapping a folder.

### Docker Compose

1. Copy `.env.example` to `.env`, set your own password and the real host folders, and never commit `.env`.
2. Run `docker compose up -d`. `compose.yaml` can also build the image from source with `docker compose build`.
3. Open `http://SERVER:8765` and sign in.

Don't expose the web UI directly to the internet. Use your private network or an authenticated reverse proxy.

## First run

1. Open **Media library** and check that your folders appear. Start with one short video.
2. Open **Speech models** and download a model. **tiny** is quick for a first test; **medium** (the default setting) is much more accurate. Choosing a model in Settings doesn't download it.
3. In **Settings**, pick the audio language (or auto) and the subtitle languages you want, then save. Languages other than English need their translation packs installed from **Language packs**.
4. Queue the video. When it finishes, preview the result, then refresh your Jellyfin library to see the new subtitle track.
5. Once you're happy, choose library subfolders to watch automatically. The first scan includes files that are already there.

## Files it writes

| Source | Result |
|---|---|
| `Show S01E01.mkv` | `Show S01E01.en.AI.srt`; regenerating or editing makes `Show S01E01.en.AI.2.srt`, and so on |
| `Song.flac` | `Song.lrc` (line-synced lyrics), `Song.txt` (plain lyrics), `Song.words.lrc` (word-by-word) |

- **Existing tracks:** subtitle languages that already have a full track are skipped by default.
- **Lyric files:** if a song already has its own `Song.lrc`, new lyrics are saved as `Song.AI.lrc` instead. Making lyrics again replaces the files Zilcho Subs wrote, unless you've edited them since.
- **Word timing in `Song.lrc`:** a setting can put the word-by-word timing into `Song.lrc` itself, for players that only read that file.
- **Editing:** subtitles and lyrics can be previewed and edited in the app.

App data (`/config`) holds settings, the job queue, models, translation packs, logs and caches. Clearing old caches and logs is up to you; stop the container first.

## Good to know

- Speech recognition makes mistakes, especially with music, overlapping voices, names and short lines. Check important subtitles before relying on them.
- Processing runs on the CPU. A full episode with the medium model can take a while on modest hardware. Larger models are more accurate but slower.
- Only one job runs at a time. Pausing waits for the current part of the audio to finish.
- Subtitle input for translation is UTF-8 `.srt` up to 2 MB. Image-based subtitles, ASS styling and OCR of on-screen text aren't supported.

## Development

Python 3.11+:

```sh
python -m venv .venv
# Activate the environment for your OS, then:
pip install -e '.[test]'
pytest -q
node --check zilcho_subs/static/app.js
```

The tests use temporary synthetic media and fake inference. They don't download models or contact any server. To run the app locally, set `ZILCHO_CONFIG`, `ZILCHO_MEDIA` and `ZILCHO_PASSWORD`, then run `python -m zilcho_subs`. The Dockerfile is the supported Linux build. It compiles FFmpeg, PyAV, CTranslate2 and NumPy from recorded, pinned sources.

Each release ships with a matching source package: the source code of every component in the image, its licenses, and build records. It's published on the release page next to the image.

## Licenses and credits

Zilcho Subs' own code is MIT licensed ([LICENSE](LICENSE)). It's built on [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and Whisper models, CTranslate2, FFmpeg (LGPL-2.1-or-later), the Argos Translate model format, FastAPI and many other open-source projects. Each keeps its own license: see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the app's **About & credits** page. Speech models, translation packs and the sound classifier are downloaded from their providers on request, under their own terms.
