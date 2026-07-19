#!/usr/bin/env python3
"""
extract_playlist.py — Local-First Playlist Extractor
=====================================================
Turns a YouTube playlist (or single video, or local file) into a clean,
chunked, paste-ready folder for distilling a creator's domain expertise
into a Claude Skill.

EVERYTHING here runs locally and FREE. No API calls. No paid services.

Pipeline per video:
    1. Get transcript  -> YouTube captions FIRST (free, instant)
                          -> Whisper fallback only if no captions
    2. (optional) Scene-frame extraction for screen-heavy creators
    3. (optional) OCR frames -> on-screen code/text becomes plain text
    4. Write clean, numbered files ready to paste into Claude Pro chat

Output layout:
    output/<playlist_name>/
        00_INDEX.md                  <- overview + paste instructions
        video_01_<slug>/
            transcript.txt
            frames/ scene_001.jpg ...
            ocr.txt                  <- text pulled off the frames
        video_02_<slug>/
            ...

------------------------------------------------------------------------
SETUP (one time, on your Mac):
    brew install yt-dlp ffmpeg tesseract
    pip install openai-whisper youtube-transcript-api imagehash pillow
    # (whisper + transcript-api are only needed for the transcript step;
    #  imagehash + pillow only for frame dedup)
------------------------------------------------------------------------

USAGE:
    # Talking-head creator (transcript only, cheapest/fastest):
    python extract_playlist.py <PLAYLIST_URL> --mode talking-head

    # Screen-heavy creator (transcript + scene frames + OCR):
    python extract_playlist.py <PLAYLIST_URL> --mode screen-heavy

    # A single video:
    python extract_playlist.py <VIDEO_URL> --mode screen-heavy

    # A local file you already have:
    python extract_playlist.py /path/to/video.mp4 --local --mode screen-heavy

OPTIONS:
    --mode {talking-head, screen-heavy}   default: talking-head
    --whisper-model {tiny,base,small,medium}   default: base
    --scene-threshold FLOAT   default: 0.4 (lower=more frames; tune per creator)
    --max-videos INT          cap how many playlist items to process
    --out DIR                 output root (default: ./output)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Pretty console ────────────────────────────────────────────────────────────
C = {
    "cyan": "\033[96m", "green": "\033[92m", "yellow": "\033[93m",
    "red": "\033[91m", "bold": "\033[1m", "dim": "\033[2m", "reset": "\033[0m",
}
def say(msg, color="reset"):  print(f"{C[color]}{msg}{C['reset']}")
def step(msg):                say(f"  → {msg}", "dim")
def ok(msg):                  say(f"  ✓ {msg}", "green")
def warn(msg):                say(f"  ! {msg}", "yellow")
def err(msg):                 say(f"  ✗ {msg}", "red")


# ── Tool availability ───────────────────────────────────────────────────────
def has(tool: str) -> bool:
    return shutil.which(tool) is not None

def require(tool: str):
    if not has(tool):
        err(f"'{tool}' not found. Install it first (see header of this script).")
        sys.exit(1)


# ── Helpers ──────────────────────────────────────────────────────────────────
def slugify(text: str, maxlen: int = 40) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:maxlen].strip("-") or "untitled"

def run(cmd: list, capture=True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=capture, text=True)


# ── Network / rate-limit options ──────────────────────────────────────────────
# Populated once in main() from CLI flags, then read by every yt-dlp call site
# and the caption fetcher. This is how we stop looking like an anonymous bot to
# YouTube (the usual cause of 429 / "confirm you're not a bot" limits).
_NET_FLAGS: list[str] = []
_PROXY: str | None = None


def _build_net_flags(args) -> list[str]:
    """Translate the CLI rate-limit flags into a yt-dlp argument list."""
    flags: list[str] = []
    if args.cookies_from_browser:
        flags += ["--cookies-from-browser", args.cookies_from_browser]
    if args.cookies:
        flags += ["--cookies", args.cookies]
    if args.proxy:
        flags += ["--proxy", args.proxy]
    if args.sleep_requests is not None:
        flags += ["--sleep-requests", str(args.sleep_requests)]
    if args.sleep_interval is not None:
        flags += ["--sleep-interval", str(args.sleep_interval)]
    if args.max_sleep_interval is not None:
        flags += ["--max-sleep-interval", str(args.max_sleep_interval)]
    if args.retries is not None:
        flags += ["--retries", str(args.retries),
                  "--extractor-retries", str(args.retries)]
    if args.limit_rate:
        flags += ["--limit-rate", args.limit_rate]
    return flags


def ytdlp(*args) -> list[str]:
    """Build a yt-dlp command with the shared network/rate-limit flags applied."""
    return ["yt-dlp", *_NET_FLAGS, *args]


def parse_video_selection(spec: str) -> list[int]:
    """Parse '10-25', '1,3,5-7', '5' into a sorted list of 1-based indices.

    Raises ValueError on malformed input. Empty/None means "no filter" — the
    caller should handle that by not calling this function.
    """
    indices: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if lo < 1 or hi < lo:
                raise ValueError(f"invalid range '{part}' (must be lo>=1 and hi>=lo)")
            indices.update(range(lo, hi + 1))
        else:
            n = int(part)
            if n < 1:
                raise ValueError(f"invalid index '{part}' (must be >= 1)")
            indices.add(n)
    if not indices:
        raise ValueError("empty selection")
    return sorted(indices)


# ── Stage 0: enumerate playlist ───────────────────────────────────────────────
def enumerate_videos(
    source: str,
    is_local: bool,
    max_videos: int | None,
    selection: list[int] | None = None,
):
    """Return list of dicts: {id, title, url} (or local file entry)."""
    if is_local:
        title = Path(source).stem
        return [{"id": "local", "title": title, "url": source, "local_path": source}]

    require("yt-dlp")
    step("Enumerating playlist via yt-dlp...")
    # --flat-playlist is fast: metadata only, no download
    res = run(ytdlp("--flat-playlist", "--dump-json", source))
    if res.returncode != 0:
        err(f"yt-dlp failed:\n{res.stderr.strip()[:400]}")
        sys.exit(1)

    videos = []
    for line in res.stdout.strip().splitlines():
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = j.get("id")
        if not vid:
            continue
        videos.append({
            "id": vid,
            "title": j.get("title") or vid,
            "url": j.get("url") or f"https://youtu.be/{vid}",
        })
    total = len(videos)
    if selection:
        kept = []
        for idx in selection:
            if 1 <= idx <= total:
                kept.append(videos[idx - 1])
            else:
                warn(f"--videos index {idx} out of range (playlist has {total}); skipping.")
        videos = kept
    if max_videos:
        videos = videos[:max_videos]
    if selection:
        ok(f"Selected {len(videos)} of {total} video(s) via --videos.")
    else:
        ok(f"Found {len(videos)} video(s).")
    return videos


# Markers that genuinely aren't speech — safe to strip.
# Other bracketed cues like [laughter], [applause], [sighs] carry meaning
# and are kept (they're rare and don't bloat token count materially).
_NOISE_BRACKETS_RE = re.compile(
    r"\[\s*(music|música|musique|musik|silence|no audio)\s*\]",
    re.IGNORECASE,
)


def _fetch_captions(video_id: str) -> tuple[str, list[dict], str] | None:
    """Fetch English captions if available; fall back to default track.

    Returns (cleaned_text, segments, language) or None if nothing usable.
    `segments` is a list of {"start": float, "end": float, "text": str}.
    """
    from youtube_transcript_api import YouTubeTranscriptApi
    # Route caption fetches through the proxy too, when configured — the
    # caption API gets IP-blocked independently of yt-dlp.
    api = None
    if _PROXY:
        try:
            from youtube_transcript_api.proxies import GenericProxyConfig
            api = YouTubeTranscriptApi(
                proxy_config=GenericProxyConfig(http_url=_PROXY, https_url=_PROXY)
            )
        except Exception:
            api = None  # older library without proxy support; fall through
    if api is None:
        api = YouTubeTranscriptApi()

    fetched = None
    language = "unknown"
    try:
        listing = api.list(video_id)
        try:
            track = listing.find_manually_created_transcript(["en", "en-US", "en-GB"])
        except Exception:
            try:
                track = listing.find_generated_transcript(["en", "en-US", "en-GB"])
            except Exception:
                track = None
        if track is not None:
            language = getattr(track, "language_code", "en")
            fetched = track.fetch()
    except Exception:
        pass

    if fetched is None:
        fetched = api.fetch(video_id)  # default track, whatever language

    segments: list[dict] = []
    for s in fetched:
        # youtube_transcript_api exposes start (float seconds) and duration.
        start = float(getattr(s, "start", 0.0))
        dur = float(getattr(s, "duration", 0.0))
        text = (s.text or "").strip()
        if not text:
            continue
        # Apply the same [Music]-style strip we do to the flat text.
        text = _NOISE_BRACKETS_RE.sub("", text).strip()
        if not text:
            continue
        segments.append({"start": start, "end": start + dur, "text": text})

    raw = " ".join(seg["text"] for seg in segments)
    cleaned = re.sub(r"\s+", " ", raw).strip()
    if not cleaned:
        return None
    return cleaned, segments, language


def _write_timestamped(
    vdir: Path,
    *,
    source: str,
    language: str,
    segments: list[dict],
) -> None:
    """Persist transcript.timestamped.json alongside transcript.txt."""
    import json
    payload = {
        "source": source,
        "language": language,
        "segments": segments,
    }
    (vdir / "transcript.timestamped.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _save_description(video: dict, vdir: Path) -> None:
    """Fetch the video description via yt-dlp and write description.txt.

    Best-effort: failures are silently skipped (descriptions are nice-to-have
    for chapter parsing, not required).
    """
    target = vdir / "description.txt"
    if target.exists():
        return
    url = video.get("url") or f"https://www.youtube.com/watch?v={video['id']}"
    res = run(ytdlp("--skip-download", "--get-description", url))
    if res.returncode == 0 and res.stdout.strip():
        try:
            target.write_text(res.stdout, encoding="utf-8")
        except OSError:
            pass


def _is_apple_silicon() -> bool:
    import platform
    return platform.system() == "Darwin" and platform.machine() in {"arm64", "aarch64"}


def _transcribe_with_whisper(media: str, model_name: str, language: str | None = None):
    """Transcribe `media` using the best available Whisper backend.

    Preference order: mlx-whisper (Apple Silicon) > faster-whisper > openai-whisper.
    All three produce the same {"text", "segments", "language"} shape so
    downstream code is backend-agnostic. Returns (backend_label, result_dict)
    or (backend_label, None) on failure.
    """
    # --- Path A0: mlx-whisper on Apple Silicon ---
    if _is_apple_silicon():
        try:
            import mlx_whisper  # type: ignore
            step(f"Transcribing with mlx-whisper ({model_name})...")
            kwargs: dict = {}
            if language:
                kwargs["language"] = language
            result = mlx_whisper.transcribe(media, path_or_hf_repo=model_name, **kwargs)
            segments = [
                {
                    "start": float(seg.get("start", 0.0)),
                    "end": float(seg.get("end", 0.0)),
                    "text": (seg.get("text") or "").strip(),
                }
                for seg in result.get("segments", [])
                if (seg.get("text") or "").strip()
            ]
            return "mlx-whisper", {
                "text": result.get("text", ""),
                "segments": segments,
                "language": result.get("language", language or "unknown"),
            }
        except ImportError:
            pass
        except Exception as e:
            warn(f"mlx-whisper failed ({type(e).__name__}: {e}); "
                 f"falling back to faster-whisper.")

    # --- Path A: faster-whisper (preferred cross-platform) ---
    try:
        from faster_whisper import WhisperModel
        step(f"Transcribing with faster-whisper ({model_name})... this can take a while")
        # int8 is the standard CPU compute_type: ~4x faster, no quality loss vs fp32
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        # vad_filter skips silence, which dramatically reduces Whisper hallucinations
        # on long pauses (a known failure mode). Free quality win.
        kwargs = {
            "beam_size": 5,
            "vad_filter": True,
            "vad_parameters": {"min_silence_duration_ms": 500},
        }
        if language:
            kwargs["language"] = language
        segments_iter, info = model.transcribe(media, **kwargs)
        segments: list[dict] = []
        text_parts: list[str] = []
        for seg in segments_iter:
            t = (seg.text or "").strip()
            if not t:
                continue
            segments.append({
                "start": float(seg.start),
                "end": float(seg.end),
                "text": t,
            })
            text_parts.append(t)
        return "faster-whisper", {
            "text": " ".join(text_parts),
            "segments": segments,
            "language": getattr(info, "language", language or "unknown"),
        }
    except ImportError:
        pass  # faster-whisper not installed; try openai-whisper
    except Exception as e:
        warn(f"faster-whisper failed ({type(e).__name__}: {e}); "
             f"falling back to openai-whisper.")

    # --- Path B: openai-whisper (fallback) ---
    try:
        import whisper  # openai-whisper
    except ImportError:
        err("No Whisper backend available and no captions. "
            "Install one of:\n"
            "  pip install mlx-whisper         # Apple Silicon\n"
            "  pip install faster-whisper      # recommended elsewhere\n"
            "  pip install openai-whisper      # fallback")
        return "whisper", None

    step(f"Transcribing with openai-whisper ({model_name})... this can take a while")
    model = whisper.load_model(model_name)
    kwargs = {"verbose": False}
    if language:
        kwargs["language"] = language
    result = model.transcribe(media, **kwargs)
    segments = [
        {
            "start": float(seg.get("start", 0.0)),
            "end": float(seg.get("end", 0.0)),
            "text": (seg.get("text") or "").strip(),
        }
        for seg in result.get("segments", [])
        if (seg.get("text") or "").strip()
    ]
    return "openai-whisper", {
        "text": result.get("text", ""),
        "segments": segments,
        "language": result.get("language", language or "unknown"),
    }


# ── Stage 1: transcript (captions first, Whisper fallback) ─────────────────────
def get_transcript(
    video: dict,
    vdir: Path,
    whisper_model: str,
    *,
    min_caption_words: int = 100,
    force_whisper: bool = False,
    language: str | None = None,
) -> bool:
    """Write transcript.txt. Return True on success."""
    tpath = vdir / "transcript.txt"

    # Path 1: YouTube captions (free, instant) — only for real YT videos
    if not force_whisper and video["id"] != "local":
        try:
            captions = _fetch_captions(video["id"])
            if captions:
                text, segments, language = captions
                word_count = len(text.split())
                if word_count < min_caption_words:
                    warn(f"Captions only {word_count} words "
                         f"(below --min-caption-words={min_caption_words}); "
                         f"falling back to Whisper.")
                else:
                    tpath.write_text(text, encoding="utf-8")
                    _write_timestamped(
                        vdir, source="youtube_captions",
                        language=language, segments=segments,
                    )
                    ok(f"Transcript via YouTube captions ({word_count:,} words) — FREE")
                    return True
        except Exception as e:
            warn(f"No captions ({type(e).__name__}); falling back to Whisper.")

    # Path 2: Whisper fallback (free, local, slower)
    # Need a media file. For YT video without captions, download audio first.
    media = video.get("local_path")
    if media is None:
        require("yt-dlp")
        step("Downloading audio for Whisper...")
        audio_out = str(vdir / "audio.%(ext)s")
        res = run(ytdlp("-x", "--audio-format", "wav",
                        "-o", audio_out, video["url"]))
        if res.returncode != 0:
            err("Audio download failed.")
            return False
        cand = list(vdir.glob("audio.*"))
        media = str(cand[0]) if cand else None
        if not media:
            err("Audio file not found after download.")
            return False

    if not has("ffmpeg"):
        require("ffmpeg")

    backend, result = _transcribe_with_whisper(media, whisper_model, language=language)
    if result is None:
        return False

    text = re.sub(r"\s+", " ", result["text"]).strip()
    tpath.write_text(text, encoding="utf-8")
    _write_timestamped(
        vdir, source=backend,
        language=result.get("language", "unknown"),
        segments=result["segments"],
    )
    ok(f"Transcript via {backend} ({len(text.split()):,} words)")
    # tidy: drop the big audio file
    for a in vdir.glob("audio.*"):
        a.unlink(missing_ok=True)
    return True


# ── Stage 2: scene frames (screen-heavy only) ──────────────────────────────────
def extract_frames(media_path: str, vdir: Path, threshold: float) -> int:
    require("ffmpeg")
    fdir = vdir / "frames"
    fdir.mkdir(exist_ok=True)
    step(f"Extracting scene-change frames (threshold={threshold})...")
    run([
        "ffmpeg", "-i", media_path,
        "-vf", f"select='gt(scene,{threshold})'",
        "-vsync", "vfr", str(fdir / "scene_%03d.jpg"), "-y",
    ])
    frames = sorted(fdir.glob("scene_*.jpg"))
    ok(f"Captured {len(frames)} scene frame(s).")
    return len(frames)


# ── Stage 2b: perceptual-hash dedup ────────────────────────────────────────────
def dedup_frames(vdir: Path) -> int:
    fdir = vdir / "frames"
    frames = sorted(fdir.glob("scene_*.jpg"))
    if len(frames) < 2:
        return len(frames)
    try:
        from PIL import Image
        import imagehash
    except ImportError:
        warn("imagehash/pillow not installed; skipping dedup. "
             "(pip install imagehash pillow)")
        return len(frames)

    step("Deduplicating near-identical frames (perceptual hash)...")
    kept, seen = [], []
    for f in frames:
        try:
            h = imagehash.phash(Image.open(f))
        except Exception:
            continue
        if any(abs(h - s) <= 5 for s in seen):   # 5 = similarity tolerance
            f.unlink(missing_ok=True)
        else:
            seen.append(h)
            kept.append(f)
    # renumber survivors
    for i, f in enumerate(kept, 1):
        f.rename(fdir / f"key_{i:03d}.jpg")
    ok(f"Kept {len(kept)} unique frame(s) after dedup.")
    return len(kept)


# ── Stage 3: OCR frames -> text (free, the cost-saving lever) ──────────────────
def ocr_frames(vdir: Path):
    require("tesseract")
    fdir = vdir / "frames"
    frames = sorted(fdir.glob("*.jpg"))
    if not frames:
        return
    step("OCR-ing frames into text (free, replaces paid vision tokens)...")
    chunks = []
    for f in frames:
        res = run(["tesseract", str(f), "stdout"])
        text = (res.stdout or "").strip()
        # keep only frames where OCR found meaningful text
        if len(text) >= 20:
            chunks.append(f"--- {f.name} ---\n{text}")
    if chunks:
        (vdir / "ocr.txt").write_text("\n\n".join(chunks), encoding="utf-8")
        ok(f"OCR text extracted from {len(chunks)} frame(s) -> ocr.txt")
    else:
        warn("No meaningful on-screen text found (likely a talking-head video).")


# ── Index / paste instructions ─────────────────────────────────────────────────
def write_index(root: Path, playlist_name: str, videos: list, mode: str):
    lines = [
        f"# Extraction: {playlist_name}",
        "",
        f"**Mode:** {mode}  |  **Videos:** {len(videos)}",
        "",
        "## How to use with Claude Pro chat (free)",
        "",
        "Work PER VIDEO first (the context window can't hold all transcripts at once):",
        "",
        "1. Open each `video_NN_*/transcript.txt`, paste into Claude, ask it to DISTILL",
        "   into structured JSON (claims, heuristics, reasoning patterns).",
        "2. For screen-heavy videos, also paste that video's `ocr.txt` and/or drag a few",
        "   `frames/key_*.jpg` in alongside the transcript.",
        "3. Save each distilled JSON.",
        "4. Once ALL videos are distilled, paste the small JSONs together and ask Claude",
        "   to SYNTHESIZE — find the patterns that RECUR across videos = the creator's",
        "   core method. That synthesis becomes the SKILL.md.",
        "",
        "## Videos",
        "",
    ]
    for i, v in enumerate(videos, 1):
        lines.append(f"{i:>2}. {v['title']}")
    (root / "00_INDEX.md").write_text("\n".join(lines), encoding="utf-8")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(
        description="Local-first playlist extractor for YouTuber-skill distillation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("source", help="Playlist URL, video URL, or local file path")
    p.add_argument("--local", action="store_true", help="Source is a local file")
    p.add_argument("--mode", choices=["talking-head", "screen-heavy"],
                   default="talking-head")
    p.add_argument("--whisper-model", default="base",
                   choices=["tiny", "base", "small", "medium"])
    p.add_argument("--scene-threshold", type=float, default=0.4)
    p.add_argument("--max-videos", type=int, default=None,
                   help="Cap to the first N videos. Combine with --videos to "
                        "further trim, or use --videos alone for ranges.")
    p.add_argument("--videos", default=None,
                   help="Select specific 1-based indices: '10-25', '5', or "
                        "'1,3,5-7'. Applied to the playlist order.")
    p.add_argument("--jobs", type=int, default=1,
                   help="Process this many videos concurrently. Safe with "
                        "captions-first extraction (IO-bound); for "
                        "screen-heavy or --force-whisper, keep at 1 unless "
                        "you have spare CPU. Default: 1.")
    p.add_argument("--out", default="output")
    p.add_argument("--playlist-name", default=None,
                   help="Output subfolder name under --out. Defaults to the "
                        "local file's stem (--local) or 'playlist' (URL "
                        "source). Set this to match the PLAYLIST_NAME you'll "
                        "use in later phases (preprocess, phase2, etc).")
    p.add_argument("--min-caption-words", type=int, default=100,
                   help="If YouTube captions produce fewer than this many words, "
                        "fall back to Whisper. Set to 0 to always trust captions.")
    p.add_argument("--force-whisper", action="store_true",
                   help="Skip YouTube captions; always use Whisper.")
    p.add_argument("--whisper-language", default=None,
                   help="ISO 639-1 language code passed to the Whisper backend "
                        "(e.g. 'en'). If omitted, the backend auto-detects.")
    p.add_argument("--no-description", action="store_true",
                   help="Skip writing description.txt per video.")

    net = p.add_argument_group(
        "network / rate-limit",
        "Options for getting past YouTube 429 / 'confirm you're not a bot' "
        "limits. Cookies make requests look logged-in; sleep/retries throttle "
        "and ride out transient blocks.",
    )
    net.add_argument("--cookies-from-browser", default=None,
                     metavar="BROWSER",
                     help="Load cookies from a local browser profile "
                          "(chrome, firefox, edge, brave, safari, ...). "
                          "The single most effective fix for bot checks.")
    net.add_argument("--cookies", default=None, metavar="FILE",
                     help="Path to a Netscape-format cookies.txt (alternative "
                          "to --cookies-from-browser).")
    net.add_argument("--proxy", default=None, metavar="URL",
                     help="Proxy URL (e.g. http://user:pass@host:port) applied "
                          "to yt-dlp AND the caption fetcher, to rotate off a "
                          "blocked IP.")
    net.add_argument("--sleep-requests", type=float, default=None,
                     metavar="SEC",
                     help="Seconds to sleep between requests (yt-dlp). "
                          "Try 1-2 when you keep hitting 429s.")
    net.add_argument("--sleep-interval", type=float, default=None,
                     metavar="SEC",
                     help="Minimum seconds to sleep before each download.")
    net.add_argument("--max-sleep-interval", type=float, default=None,
                     metavar="SEC",
                     help="Upper bound for randomized download sleep "
                          "(pairs with --sleep-interval).")
    net.add_argument("--retries", type=int, default=10, metavar="N",
                     help="Retries for both downloads and extraction. "
                          "Default 10; pure upside on transient 429s.")
    net.add_argument("--limit-rate", default=None, metavar="RATE",
                     help="Cap download bandwidth (e.g. 2M, 500K) — looks less "
                          "bot-like than maxing the pipe.")
    args = p.parse_args()

    # Publish network options for every yt-dlp call site + the caption fetcher.
    global _NET_FLAGS, _PROXY
    _NET_FLAGS = _build_net_flags(args)
    _PROXY = args.proxy

    say(f"\n{C['bold']}{C['cyan']}━━━ Local Playlist Extractor ━━━{C['reset']}")
    say(f"{C['dim']}Mode: {args.mode}  |  Source: {args.source}{C['reset']}\n")

    selection: list[int] | None = None
    if args.videos:
        try:
            selection = parse_video_selection(args.videos)
        except ValueError as e:
            err(f"--videos: {e}")
            sys.exit(2)

    if args.jobs < 1:
        err("--jobs must be >= 1")
        sys.exit(2)

    videos = enumerate_videos(args.source, args.local, args.max_videos, selection)
    if not videos:
        err("No videos to process.")
        sys.exit(1)

    playlist_name = slugify(
        args.playlist_name if args.playlist_name
        else (Path(args.source).stem if args.local else "playlist")
    )
    root = Path(args.out) / playlist_name
    root.mkdir(parents=True, exist_ok=True)

    def process_one(i: int, v: dict) -> None:
        say(f"\n{C['bold']}[{i}/{len(videos)}] {v['title']}{C['reset']}")
        vdir = root / f"video_{i:02d}_{slugify(v['title'])}"
        vdir.mkdir(exist_ok=True)

        # 0. description (cheap, enables chapter-aware preprocessing)
        if not args.no_description and v["id"] != "local":
            _save_description(v, vdir)

        # 1. transcript
        if not get_transcript(
            v, vdir, args.whisper_model,
            min_caption_words=args.min_caption_words,
            force_whisper=args.force_whisper,
            language=args.whisper_language,
        ):
            warn("Skipping video (no transcript).")
            return

        # 2+3. visuals — only for screen-heavy + only if we have a media file
        if args.mode == "screen-heavy":
            media = v.get("local_path")
            if media is None:
                # download the video for frame extraction
                if has("yt-dlp"):
                    step("Downloading video for frame extraction...")
                    out_tmpl = str(vdir / "video.%(ext)s")
                    run(ytdlp("-f", "worst[ext=mp4]/worst",
                              "-o", out_tmpl, v["url"]))
                    cand = list(vdir.glob("video.*"))
                    media = str(cand[0]) if cand else None
            if media and Path(media).exists():
                extract_frames(media, vdir, args.scene_threshold)
                dedup_frames(vdir)
                ocr_frames(vdir)
                # drop the downloaded video to save disk (keep local originals)
                if v.get("local_path") is None:
                    for f in vdir.glob("video.*"):
                        f.unlink(missing_ok=True)
            else:
                warn("No media available for frames; transcript only.")

    # Parallel only when explicitly asked. Whisper + ffmpeg are CPU-bound,
    # so high --jobs on screen-heavy or --force-whisper will thrash; the
    # captions-first IO-bound path is the safe place to crank this up.
    if args.jobs > 1:
        if args.mode == "screen-heavy" or args.force_whisper:
            warn(f"--jobs={args.jobs} with mode={args.mode} / force-whisper "
                 f"may saturate CPU; consider --jobs 1 if your machine struggles.")
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futures = [ex.submit(process_one, i, v) for i, v in enumerate(videos, 1)]
            for fut in as_completed(futures):
                # Surface worker exceptions instead of silently dropping them.
                fut.result()
    else:
        for i, v in enumerate(videos, 1):
            process_one(i, v)

    write_index(root, playlist_name, videos, args.mode)
    say(f"\n{C['bold']}{C['green']}━━━ Done ━━━{C['reset']}")
    say(f"Output: {C['cyan']}{root}{C['reset']}")
    say(f"{C['dim']}Open {root}/00_INDEX.md for paste instructions.{C['reset']}\n")


if __name__ == "__main__":
    main()
