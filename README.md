# suno-dl

Save a Suno share-link to stream-quality audio for personal backup.

```bash
suno-dl "https://suno.com/s/XXXXXXX"
suno-dl "https://suno.com/song/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" -o ~/Music/mytrack.m4a
```

Free-tier official exports got much tighter since September, and the
Download button often shows `0 downloads left`. This tool saves what the
web player already plays (stream quality, ~128 kbps M4A), it does not
unlock premium WAV/MP3/Stems exports.

> Use only for songs you created or have permission to save.
> Educational / personal-backup use. Respect creators and Suno's Terms.

## How it works

1. Resolves `/s/<share>` or `/song/<uuid>` to a clip UUID via the canonical `/song/<uuid>` URL.
2. Gets the title via `studio-api-prod.suno.com/api/oembed`.
3. Requests playback rights via `POST studio-api-prod.suno.com/api/mango/rights`
   (same request the web player makes).
4. Downloads the encrypted stream from CloudFront:
   `https://d2lwuy8qc234o3.cloudfront.net/1/clip/<uuid>.m4a`
5. Unwraps the content key with `SHA256(glt)` + AES-GCM, then decrypts
   audio with AES-CTR — identical to browser playback — to a valid `.m4a`.

## Install

Requires Python 3.9+.

```bash
git clone https://github.com/4mrco/suno-dl
cd suno-dl
python3 -m pip install .
# then:
suno-dl "https://suno.com/s/XXXXXXX"
```

For `--mp3` you also need ffmpeg on PATH:

```bash
# Fedora / Nobara
sudo dnf install ffmpeg
# Debian / Ubuntu
sudo apt install ffmpeg
# macOS
brew install ffmpeg
```

## Usage

```bash
# single, auto-named from title
suno-dl "https://suno.com/s/XXXXXXX"

# custom output dir
suno-dl "https://suno.com/s/XXXXXXX" --out-dir ~/Music/Suno

# custom filename
suno-dl "https://suno.com/song/xxxxxxxx-..." -o track.m4a

# batch file (one link per line, # = comment)
suno-dl --batch links.txt --out-dir ~/Music/Suno

# mp3 convert via ffmpeg (keeps .m4a by default)
suno-dl "https://suno.com/s/XXXXXXX" --mp3 --mp3-bitrate 192k

# mp3 only
suno-dl "https://suno.com/s/XXXXXXX" --mp3 --no-keep-m4a

# direct mp3 filename implies --mp3
suno-dl "https://suno.com/s/XXXXXXX" -o track.mp3

# skip extras
suno-dl "https://suno.com/s/XXXXXXX" --no-cover --no-meta
```

Each download writes alongside the audio:
- `Title.m4a` — decrypted stream audio
- `Title.cover.jpeg` — cover art (if available)
- `Title.json` — `{title, clip_id, source_url, song_url}`

## Limitations

- Stream quality only. For full-quality WAV / MP3 / stems, use Suno's
  official Download once your quota resets.
- Suno can change the `mango/rights` or CDN layout at any time.
  If you get `404` / decryption errors after a Suno update, open an issue.
- No login required today. If Suno starts requiring auth for rights,
  you'll need to add your session token.

## Legal

MIT. No music is hosted here. Downloading other people's tracks without
permission may violate copyright and Suno's Terms of Service. The authors
are not responsible for misuse.

## Roadmap

- [x] `--mp3` convert via ffmpeg
- [ ] `--lyrics` .lrc export when available
- [ ] playlist / profile support
- [ ] retry + resume for batch
