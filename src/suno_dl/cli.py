#!/usr/bin/env python3
"""suno-dl CLI: save a Suno share-link to stream-quality M4A.

What it does:
  1. Resolves /s/<share> or /song/<uuid> to a clip UUID
  2. Fetches the title via the oembed endpoint
  3. Requests playback rights (mango/rights) – same as the web player
  4. Downloads the encrypted stream from CloudFront
  5. Decrypts AES-GCM-wrapped key + AES-CTR audio, same as browser playback

Output is stream quality (~128 kbps M4A), not the premium
MP3/WAV/Stems export. For studio exports use Suno's official
Download button when your quota allows.

Use only for songs you created or have permission to save,
for personal backup / fair use.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import Counter

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
SONG_RE = re.compile(
    r"/song/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)
RIGHTS_URL = "https://studio-api-prod.suno.com/api/mango/rights"
CDN_TEMPLATE = "https://d2lwuy8qc234o3.cloudfront.net/1/clip/{clip}.m4a"
CDN_FALLBACK = "https://d2lwuy8qc234o3.cloudfront.net/1/clip/{clip}.webm"


def http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": "https://suno.com/",
            "Origin": "https://suno.com",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_post_json(url: str, payload: dict, timeout: int = 20) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": UA,
            "Origin": "https://suno.com",
            "Referer": "https://suno.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def resolve_clip_id(link: str) -> str:
    """Accept /song/<uuid> directly, else fetch /s/<share> HTML."""
    m = SONG_RE.search(link)
    if m:
        return m.group(1).lower()
    html = http_get(link).decode(errors="ignore")
    m = SONG_RE.search(html)
    if m:
        return m.group(1).lower()
    all_uuids = [u.lower() for u in UUID_RE.findall(html)]
    if all_uuids:
        return Counter(all_uuids).most_common(1)[0][0]
    raise ValueError("Could not find a song UUID in that link.")


def get_title(clip: str) -> str:
    oembed = "https://studio-api-prod.suno.com/api/oembed?url=" + urllib.parse.quote(
        f"https://suno.com/song/{clip}", safe=""
    )
    try:
        data = json.loads(http_get(oembed).decode())
        return data.get("title", clip[:8])
    except Exception:
        return clip[:8]


def sanitize(name: str) -> str:
    name = name.strip().replace("/", "_").replace("\\", "_")
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)
    return name[:80] or "suno_track"


def decrypt_clip(clip: str, enc: bytes, rights: dict) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    user_key = hashlib.sha256(rights["glt"].encode()).digest()

    def unwrap(pkg_b64: str) -> bytes:
        raw = base64.b64decode(pkg_b64)
        nonce, ct = raw[:12], raw[12:]
        return AESGCM(user_key).decrypt(nonce, ct, clip.encode())

    content_key = unwrap(rights["key"])
    content_iv = unwrap(rights["iv"])
    d = Cipher(algorithms.AES(content_key), modes.CTR(content_iv)).decryptor()
    return d.update(enc) + d.finalize()


def convert_to_mp3(m4a_path: pathlib.Path, bitrate: str = "192k") -> pathlib.Path:
    """Convert decrypted .m4a to .mp3 via ffmpeg. Requires ffmpeg on PATH."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found. Install it first, e.g.: "
            "sudo dnf install ffmpeg  /  sudo apt install ffmpeg  /  brew install ffmpeg"
        )
    mp3_path = m4a_path.with_suffix(".mp3")
    if m4a_path.suffix.lower() == ".mp3":
        return m4a_path
    cmd = [ffmpeg, "-y", "-v", "error", "-i", str(m4a_path),
           "-codec:a", "libmp3lame", "-b:a", bitrate, str(mp3_path)]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        # fallback: let ffmpeg pick the mp3 encoder
        cmd = [ffmpeg, "-y", "-v", "error", "-i", str(m4a_path),
               "-b:a", bitrate, str(mp3_path)]
        subprocess.run(cmd, check=True)
    return mp3_path


def download_one(link: str, out_dir: pathlib.Path, output: str | None,
                  no_cover: bool, no_meta: bool,
                  want_mp3: bool = False, mp3_bitrate: str = "192k",
                  keep_m4a: bool = True) -> pathlib.Path:
    clip = resolve_clip_id(link)
    print(f"[+] clip: {clip}")
    title = get_title(clip)
    print(f"[+] title: {title}")
    base = sanitize(title)

    # If user asked for -o track.mp3 directly, imply --mp3
    if output and pathlib.Path(output).suffix.lower() == ".mp3":
        want_mp3 = True

    if output:
        requested = pathlib.Path(output)
        out_dir = requested.parent
        if requested.suffix.lower() == ".mp3":
            out_audio = requested.with_suffix(".m4a")
        else:
            out_audio = requested
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_audio = out_dir / f"{base}.m4a"

    rights = http_post_json(
        RIGHTS_URL,
        {"content_params": {"content_id": clip, "content_type": "clip"}},
    )
    if not all(k in rights for k in ("key", "iv", "glt")):
        raise RuntimeError(f"Unexpected rights response: {rights}")

    try:
        enc = http_get(CDN_TEMPLATE.format(clip=clip))
    except Exception:
        enc = http_get(CDN_FALLBACK.format(clip=clip))
    print(f"[+] downloaded {len(enc)} encrypted bytes, decrypting...")

    plain = decrypt_clip(clip, enc, rights)
    if b"ftyp" not in plain[:50] and b"\x1a\x45\xdf\xa3" not in plain[:10]:
        raise RuntimeError("Decryption failed – keys expire fast, try again.")

    out_audio.parent.mkdir(parents=True, exist_ok=True)
    out_audio.write_bytes(plain)
    print(f"[OK] audio: {out_audio}")

    final_path = out_audio
    if want_mp3:
        print(f"[+] converting to mp3 ({mp3_bitrate}) via ffmpeg...")
        mp3_path = convert_to_mp3(out_audio, bitrate=mp3_bitrate)
        # if user asked -o something.mp3, move to that exact name
        if output and pathlib.Path(output).suffix.lower() == ".mp3":
            requested_mp3 = pathlib.Path(output)
            if mp3_path != requested_mp3:
                mp3_path.replace(requested_mp3)
                mp3_path = requested_mp3
        print(f"[OK] mp3: {mp3_path}")
        final_path = mp3_path
        if not keep_m4a:
            try:
                out_audio.unlink()
            except FileNotFoundError:
                pass
            # point cover/meta at mp3 basename
            out_audio = mp3_path

    if not no_cover:
        try:
            for ext in ("jpeg", "png"):
                try:
                    img = http_get(f"https://cdn2.suno.ai/image_{clip}.{ext}")
                    out_img = out_audio.with_suffix(f".cover.{ext}")
                    out_img.write_bytes(img)
                    print(f"[OK] cover: {out_img}")
                    break
                except Exception:
                    continue
        except Exception as e:
            print(f"[!] cover skip: {e}")

    if not no_meta:
        meta = {
            "title": title,
            "clip_id": clip,
            "source_url": link,
            "song_url": f"https://suno.com/song/{clip}",
        }
        out_json = out_audio.with_suffix(".json")
        out_json.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        print(f"[OK] meta: {out_json}")

    return final_path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="suno-dl",
        description="Save Suno share-links to stream-quality M4A (personal backup).",
    )
    ap.add_argument("link", nargs="?", help="https://suno.com/s/... or https://suno.com/song/<uuid>")
    ap.add_argument("-o", "--output", help="output file (.m4a or .mp3, default: <out-dir>/<title>.m4a)")
    ap.add_argument("--out-dir", default=str(pathlib.Path.home() / "Music" / "Suno"),
                    help="default output folder")
    ap.add_argument("--batch", help="text file with one Suno link per line")
    ap.add_argument("--no-cover", action="store_true", help="skip cover art")
    ap.add_argument("--no-meta", action="store_true", help="skip .json metadata")
    ap.add_argument("--mp3", action="store_true", help="also convert to .mp3 via ffmpeg")
    ap.add_argument("--mp3-bitrate", default="192k", help="mp3 bitrate (default: 192k)")
    ap.add_argument("--no-keep-m4a", action="store_true",
                    help="with --mp3, delete the intermediate .m4a")
    args = ap.parse_args(argv)

    if not args.link and not args.batch:
        ap.print_help()
        sys.exit(2)

    links: list[str] = []
    if args.batch:
        links += [l.strip() for l in pathlib.Path(args.batch).read_text().splitlines()
                  if l.strip() and not l.strip().startswith("#")]
    if args.link:
        links.append(args.link)

    out_dir = pathlib.Path(args.out_dir)
    failed = 0
    for i, link in enumerate(links, 1):
        print(f"\n=== [{i}/{len(links)}] {link} ===")
        try:
            # only honor -o for single download
            out = args.output if len(links) == 1 else None
            download_one(link, out_dir, out, args.no_cover, args.no_meta,
                         want_mp3=args.mp3, mp3_bitrate=args.mp3_bitrate,
                         keep_m4a=not args.no_keep_m4a)
        except Exception as e:
            failed += 1
            print(f"[FAIL] {e}", file=sys.stderr)
    if failed:
        sys.exit(1)
    print("\nDone. Stream quality only – for WAV/MP3 use Suno's official export.")


if __name__ == "__main__":
    main()
