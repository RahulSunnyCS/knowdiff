#!/usr/bin/env python3
"""
extract_playlist.py — Local-First Playlist Extractor
=====================================================
Turns a YouTube playlist (or single video, or local file) into a clean,
chunked, paste-ready folder for distilling a creator's domain expertise
into a Claude Skill.

EVERYTHING here runs locally and FREE. No API calls. No paid services.

Pipeline per video:
    1. ONE yt-dlp call  -> info JSON + description + caption track (json3)
                           (captions first; Whisper fallback only if none)
    2. (optional) Scene-frame extraction for screen-heavy creators
    3. (optional) OCR frames -> on-screen code/text becomes plain text
    4. Write clean, numbered files ready to paste into Claude Pro chat

Output layout:
    output/<playlist_name>/
        00_INDEX.md                  <- overview + paste instructions
        video_01_<slug>/
            source.info.json         <- raw yt-dlp metadata (cache, never refetched)
            source.<lang>.json3      <- raw caption track (cache, never refetched)
            metadata.json            <- id / title / url / duration / caption source
            description.txt          <- video description (chapter timestamps live here)
            transcript.txt           <- flat text
            transcript.timestamped.json  <- segment-level start/end for each line
            frames/ scene_001.jpg ...
            ocr.txt                  <- text pulled off the frames
        video_02_<slug>/
            ...

Rate-limit posture (see README "Rate limits"):
    * Idempotent: a video whose transcript + timestamped sidecar already
      exist is never refetched. Re-running after a block only touches the
      videos that are still missing. `--force` overrides.
    * One network call per video instead of three to five.
    * Paced by default (`--sleep-requests`, `--sleep-subtitles`, `--pause-sec`).
    * Stops the run on the first sign of a block ("Sign in to confirm
      you're not a bot", HTTP 429) instead of hammering, which only
      extends the block. Exit code 3 means "blocked; resume later".
    * `--cookies-from-browser`, `--extractor-args` and `--yt-dlp-args`
      pass straight through to yt-dlp.

------------------------------------------------------------------------
SETUP (one time):
    brew install yt-dlp ffmpeg tesseract        # macOS
    pip install faster-whisper imagehash pillow # optional extras
------------------------------------------------------------------------

USAGE:
    # Talking-head creator (transcript only, cheapest/fastest):
    python extract_playlist.py <PLAYLIST_URL> --playlist-name mycreator --mode talking-head

    # Screen-heavy creator (transcript + scene frames + OCR):
    python extract_playlist.py <PLAYLIST_URL> --playlist-name mycreator --mode screen-heavy

    # A single video:
    python extract_playlist.py <VIDEO_URL> --playlist-name mycreator

    # A local file you already have:
    python extract_playlist.py /path/to/video.mp4 --local --mode screen-heavy
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
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


# Exit code for "YouTube blocked us; nothing is wrong with the pipeline,
# resume later". Distinct from 1 (real failure) so wrappers can tell.
EXIT_BLOCKED = 3


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


# ── yt-dlp wrapper: pacing, identity, block detection ─────────────────────────

# Substrings (lower-cased) in yt-dlp output that mean "YouTube is refusing
# us", as opposed to "this one video is broken". Any of these stops the run.
BLOCK_SIGNATURES = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you’re not a bot",
    "http error 429",
    "too many requests",
    "rate-limited",
    "rate limited",
    "requests from your ip",
)


class BlockedError(RuntimeError):
    """Raised when yt-dlp output looks like a YouTube block / rate limit."""


def looks_blocked(text: str) -> bool:
    lowered = (text or "").lower()
    return any(sig in lowered for sig in BLOCK_SIGNATURES)


@dataclass
class YtDlp:
    """Builds every yt-dlp invocation with the same pacing + identity flags."""
    sleep_requests: float = 1.5
    sleep_subtitles: float = 2.0
    cookies_from_browser: str | None = None
    cookies_file: str | None = None
    extractor_args: str | None = None
    extra_args: list[str] = field(default_factory=list)
    binary: str = "yt-dlp"

    def base(self) -> list[str]:
        cmd = [self.binary, "--no-progress", "--no-playlist"]
        if self.sleep_requests > 0:
            cmd += ["--sleep-requests", str(self.sleep_requests)]
        if self.sleep_subtitles > 0:
            cmd += ["--sleep-subtitles", str(self.sleep_subtitles)]
        if self.cookies_from_browser:
            cmd += ["--cookies-from-browser", self.cookies_from_browser]
        if self.cookies_file:
            cmd += ["--cookies", self.cookies_file]
        if self.extractor_args:
            cmd += ["--extractor-args", self.extractor_args]
        cmd += list(self.extra_args)
        return cmd

    def run(self, args: list[str]) -> subprocess.CompletedProcess:
        """Run yt-dlp; raise BlockedError if the output looks like a block."""
        res = run(self.base() + args)
        combined = (res.stderr or "") + "\n" + (res.stdout or "")
        if looks_blocked(combined):
            raise BlockedError(combined.strip()[-600:])
        return res


# ── Stage 0: enumerate playlist ───────────────────────────────────────────────
def enumerate_videos(source: str, is_local: bool, max_videos: int | None):
    """Return list of dicts: {id, title, url} (or local file entry)."""
    if is_local:
        title = Path(source).stem
        return [{"id": "local", "title": title, "url": source, "local_path": source}]

    require("yt-dlp")
    step("Enumerating playlist via yt-dlp...")
    # --flat-playlist is fast: metadata only, no download. One request.
    res = run(["yt-dlp", "--flat-playlist", "--dump-json", source])
    if res.returncode != 0:
        if looks_blocked(res.stderr):
            raise BlockedError(res.stderr.strip()[-600:])
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
    if max_videos:
        videos = videos[:max_videos]
    ok(f"Found {len(videos)} video(s).")
    return videos


# Markers that genuinely aren't speech — safe to strip.
# Other bracketed cues like [laughter], [applause], [sighs] carry meaning
# and are kept (they're rare and don't bloat token count materially).
_NOISE_BRACKETS_RE = re.compile(
    r"\[\s*(music|música|musique|musik|silence|no audio)\s*\]",
    re.IGNORECASE,
)


# ── Captions: json3 parsing ───────────────────────────────────────────────────
def parse_json3(text: str) -> list[dict]:
    """Turn a YouTube json3 caption track into [{start, end, text}, ...].

    json3 is a list of `events`, each with `tStartMs`, `dDurationMs` and a
    list of `segs` carrying `utf8` fragments. Events without `segs` are
    window/positioning records and carry no speech. Works for both manual
    and auto-generated tracks.
    """
    data = json.loads(text)
    segments: list[dict] = []
    for ev in data.get("events", []) or []:
        segs = ev.get("segs")
        if not segs:
            continue
        joined = "".join((s.get("utf8") or "") for s in segs)
        joined = joined.replace("\n", " ")
        joined = _NOISE_BRACKETS_RE.sub("", joined)
        joined = re.sub(r"\s+", " ", joined).strip()
        if not joined:
            continue
        start = float(ev.get("tStartMs", 0)) / 1000.0
        dur = float(ev.get("dDurationMs", 0) or 0) / 1000.0
        segments.append({"start": start, "end": start + dur, "text": joined})
    return segments


def _caption_lang_from_name(path: Path) -> str:
    # source.en.json3 -> "en"; source.en-US.json3 -> "en-US"
    parts = path.name.split(".")
    return parts[1] if len(parts) >= 3 else "unknown"


def pick_caption_file(candidates: list[Path], preferred: str = "en") -> Path | None:
    """Prefer the exact preferred language, then regional variants, then
    anything starting with it, then anything at all."""
    if not candidates:
        return None
    pref = preferred.lower()
    def rank(p: Path) -> tuple[int, str]:
        lang = _caption_lang_from_name(p).lower()
        if lang == pref:
            return (0, lang)
        if lang.endswith("-orig"):          # auto "original audio" track: last resort in-language
            return (2, lang)
        if lang.startswith(pref + "-"):
            return (1, lang)
        if lang.startswith(pref):
            return (2, lang)
        return (3, lang)
    return sorted(candidates, key=rank)[0]


def _write_timestamped(
    vdir: Path,
    *,
    source: str,
    language: str,
    segments: list[dict],
) -> None:
    """Persist transcript.timestamped.json alongside transcript.txt."""
    payload = {
        "source": source,
        "language": language,
        "segments": segments,
    }
    (vdir / "transcript.timestamped.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def transcript_is_cached(vdir: Path) -> bool:
    """True when both transcript artifacts exist and are non-empty."""
    t = vdir / "transcript.txt"
    s = vdir / "transcript.timestamped.json"
    try:
        return t.exists() and s.exists() and t.stat().st_size > 0 and s.stat().st_size > 0
    except OSError:
        return False


# ── Stage 1a: one yt-dlp pass -> info.json + description + captions ──────────
def fetch_video_assets(
    video: dict,
    vdir: Path,
    ytdlp: YtDlp,
    *,
    sub_langs: str = "en.*,en",
) -> dict:
    """Fetch metadata + captions for one video in a single yt-dlp call.

    Writes source.info.json, source.<lang>.json3 (when captions exist),
    description.txt and metadata.json. Returns the parsed info dict (may
    be empty on failure). Raises BlockedError on a YouTube block.

    Idempotent: if source.info.json already exists, nothing is fetched.
    """
    info_path = vdir / "source.info.json"
    url = video.get("url") or f"https://www.youtube.com/watch?v={video['id']}"

    if not info_path.exists():
        require("yt-dlp")
        step("Fetching metadata + captions (one yt-dlp call)...")
        out_tpl = str(vdir / "source.%(ext)s")
        res = ytdlp.run([
            "--skip-download",
            "--write-info-json",
            "--write-subs", "--write-auto-subs",
            "--sub-langs", sub_langs,
            "--sub-format", "json3",
            "-o", out_tpl,
            url,
        ])
        if res.returncode != 0 and not info_path.exists():
            warn(f"yt-dlp returned {res.returncode}: {res.stderr.strip()[-300:]}")

    info: dict = {}
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            info = {}

    # description.txt (chapter timestamps live here; preprocessor reads it)
    desc = info.get("description")
    if desc and not (vdir / "description.txt").exists():
        (vdir / "description.txt").write_text(desc, encoding="utf-8")

    # metadata.json — capture_screenshots.py reads `url` from here
    meta_path = vdir / "metadata.json"
    if not meta_path.exists():
        meta = {
            "id": info.get("id") or video.get("id"),
            "title": info.get("title") or video.get("title"),
            "url": info.get("webpage_url") or url,
            "duration": info.get("duration"),
            "uploader": info.get("uploader") or info.get("channel"),
            "upload_date": info.get("upload_date"),
            "manual_sub_langs": sorted((info.get("subtitles") or {}).keys()),
            "auto_sub_langs": sorted((info.get("automatic_captions") or {}).keys()),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def captions_from_cache(vdir: Path, info: dict, preferred_lang: str = "en") -> tuple[str, list[dict], str, str] | None:
    """Read the cached json3 track. Returns (text, segments, language, source)
    or None when no usable track is on disk."""
    files = sorted(vdir.glob("source.*.json3"))
    chosen = pick_caption_file(files, preferred=preferred_lang)
    if chosen is None:
        return None
    try:
        segments = parse_json3(chosen.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        warn(f"Could not parse {chosen.name}: {type(e).__name__}")
        return None
    if not segments:
        return None
    lang = _caption_lang_from_name(chosen)
    manual = lang in (info.get("subtitles") or {})
    source = "youtube_captions" if manual else "youtube_auto_captions"
    text = re.sub(r"\s+", " ", " ".join(s["text"] for s in segments)).strip()
    return text, segments, lang, source


# ── Whisper backends ──────────────────────────────────────────────────────────
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
    ytdlp: YtDlp,
    *,
    min_caption_words: int = 100,
    force_whisper: bool = False,
    language: str | None = None,
    caption_lang: str = "en",
) -> bool:
    """Write transcript.txt + transcript.timestamped.json. Return True on success.

    May raise BlockedError (propagated to main, which stops the run)."""
    tpath = vdir / "transcript.txt"

    # Path 1: YouTube captions from the single yt-dlp pass — only for real YT videos
    if not force_whisper and video["id"] != "local":
        info = fetch_video_assets(video, vdir, ytdlp, sub_langs=f"{caption_lang}.*,{caption_lang}")
        cached = captions_from_cache(vdir, info, preferred_lang=caption_lang)
        if cached:
            text, segments, lang, source = cached
            word_count = len(text.split())
            if word_count < min_caption_words:
                warn(f"Captions only {word_count} words "
                     f"(below --min-caption-words={min_caption_words}); "
                     f"falling back to Whisper.")
            else:
                tpath.write_text(text, encoding="utf-8")
                _write_timestamped(vdir, source=source, language=lang, segments=segments)
                kind = "manual" if source == "youtube_captions" else "auto"
                ok(f"Transcript via YouTube {kind} captions [{lang}] ({word_count:,} words) — FREE")
                return True
        else:
            warn("No caption track available; falling back to Whisper.")

    # Path 2: Whisper fallback (free, local, slower)
    # Need a media file. For YT video without captions, download audio first.
    media = video.get("local_path")
    if media is None:
        cand = list(vdir.glob("audio.*"))
        if cand:
            media = str(cand[0])
        else:
            require("yt-dlp")
            step("Downloading audio for Whisper...")
            audio_out = str(vdir / "audio.%(ext)s")
            res = ytdlp.run(["-x", "--audio-format", "wav", "-o", audio_out, video["url"]])
            if res.returncode != 0:
                err(f"Audio download failed: {res.stderr.strip()[-300:]}")
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


def resolve_playlist_name(source: str, is_local: bool, explicit: str | None) -> str:
    if explicit:
        return slugify(explicit)
    return slugify(Path(source).stem if is_local else "playlist")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Local-first playlist extractor for YouTuber-skill distillation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("source", help="Playlist URL, video URL, or local file path")
    p.add_argument("--local", action="store_true", help="Source is a local file")
    p.add_argument("--playlist-name", default=None,
                   help="Output folder name under --out (default: 'playlist', or the "
                        "file stem for --local). Match PLAYLIST_NAME used by later phases.")
    p.add_argument("--mode", choices=["talking-head", "screen-heavy"],
                   default="talking-head")
    p.add_argument("--whisper-model", default="base",
                   choices=["tiny", "base", "small", "medium"])
    p.add_argument("--scene-threshold", type=float, default=0.4)
    p.add_argument("--max-videos", type=int, default=None)
    p.add_argument("--out", default="output")
    p.add_argument("--force", action="store_true",
                   help="Refetch even when a transcript is already cached.")
    p.add_argument("--min-caption-words", type=int, default=100,
                   help="If YouTube captions produce fewer than this many words, "
                        "fall back to Whisper. Set to 0 to always trust captions.")
    p.add_argument("--force-whisper", action="store_true",
                   help="Skip YouTube captions; always use Whisper.")
    p.add_argument("--whisper-language", default=None,
                   help="ISO 639-1 language code passed to the Whisper backend "
                        "(e.g. 'en'). If omitted, the backend auto-detects.")
    p.add_argument("--caption-lang", default="en",
                   help="Preferred caption language (ISO 639-1). Regional variants "
                        "and auto-captions in that language are accepted too.")
    # Rate-limit posture
    p.add_argument("--sleep-requests", type=float, default=1.5,
                   help="Seconds yt-dlp sleeps between data-extraction requests.")
    p.add_argument("--sleep-subtitles", type=float, default=2.0,
                   help="Seconds yt-dlp sleeps before each subtitle download.")
    p.add_argument("--pause-sec", type=float, default=2.0,
                   help="Seconds to pause between videos that hit the network "
                        "(plus up to 1s jitter). Cached videos do not pause.")
    p.add_argument("--cookies-from-browser", default=None,
                   help="Passed to yt-dlp, e.g. 'chrome' or 'firefox:default'. "
                        "Use a profile that is signed in to YouTube.")
    p.add_argument("--cookies", default=None,
                   help="Passed to yt-dlp: path to a Netscape cookies.txt.")
    p.add_argument("--extractor-args", default=None,
                   help="Passed to yt-dlp, e.g. 'youtube:player_client=tv,web_safari'.")
    p.add_argument("--yt-dlp-args", default="",
                   help="Any extra raw yt-dlp flags, shell-quoted as one string.")
    return p


# ── Main ───────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    say(f"\n{C['bold']}{C['cyan']}━━━ Local Playlist Extractor ━━━{C['reset']}")
    say(f"{C['dim']}Mode: {args.mode}  |  Source: {args.source}{C['reset']}\n")

    ytdlp = YtDlp(
        sleep_requests=args.sleep_requests,
        sleep_subtitles=args.sleep_subtitles,
        cookies_from_browser=args.cookies_from_browser,
        cookies_file=args.cookies,
        extractor_args=args.extractor_args,
        extra_args=shlex.split(args.yt_dlp_args) if args.yt_dlp_args else [],
    )

    playlist_name = resolve_playlist_name(args.source, args.local, args.playlist_name)
    root = Path(args.out) / playlist_name

    try:
        videos = enumerate_videos(args.source, args.local, args.max_videos)
    except BlockedError as e:
        err("YouTube blocked the playlist listing. Nothing was written.")
        say(f"{C['dim']}{e}{C['reset']}")
        return EXIT_BLOCKED
    if not videos:
        err("No videos to process.")
        return 1

    root.mkdir(parents=True, exist_ok=True)

    done = cached = failed = 0
    blocked_at: str | None = None
    for i, v in enumerate(videos, 1):
        say(f"\n{C['bold']}[{i}/{len(videos)}] {v['title']}{C['reset']}")
        vdir = root / f"video_{i:02d}_{slugify(v['title'])}"
        vdir.mkdir(exist_ok=True)
        touched_network = False

        # 1. transcript (idempotent)
        if transcript_is_cached(vdir) and not args.force:
            ok("Transcript cached — skipping fetch (use --force to refetch).")
            cached += 1
        else:
            if args.force:
                for stale in ("source.info.json", "transcript.txt", "transcript.timestamped.json"):
                    (vdir / stale).unlink(missing_ok=True)
                for stale in vdir.glob("source.*.json3"):
                    stale.unlink(missing_ok=True)
            touched_network = v["id"] != "local"
            try:
                got = get_transcript(
                    v, vdir, args.whisper_model, ytdlp,
                    min_caption_words=args.min_caption_words,
                    force_whisper=args.force_whisper,
                    language=args.whisper_language,
                    caption_lang=args.caption_lang,
                )
            except BlockedError as e:
                blocked_at = vdir.name
                err("YouTube is blocking requests (rate limit / bot check). Stopping "
                    "now — continuing would extend the block.")
                say(f"{C['dim']}{e}{C['reset']}")
                break
            if not got:
                warn("Skipping video (no transcript).")
                failed += 1
                continue
            done += 1

        # 2+3. visuals — only for screen-heavy + only if we have a media file
        if args.mode == "screen-heavy" and not (vdir / "frames").exists():
            media = v.get("local_path")
            if media is None and has("yt-dlp"):
                step("Downloading video for frame extraction...")
                out_tmpl = str(vdir / "video.%(ext)s")
                try:
                    ytdlp.run(["-f", "worst[ext=mp4]/worst", "-o", out_tmpl, v["url"]])
                except BlockedError as e:
                    blocked_at = vdir.name
                    err("YouTube is blocking the video download. Stopping.")
                    say(f"{C['dim']}{e}{C['reset']}")
                    break
                touched_network = True
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

        if touched_network and args.pause_sec > 0 and i < len(videos):
            time.sleep(args.pause_sec + random.uniform(0, 1.0))

    write_index(root, playlist_name, videos, args.mode)

    if blocked_at:
        say(f"\n{C['bold']}{C['yellow']}━━━ Stopped: blocked by YouTube ━━━{C['reset']}")
        say(f"Progress is saved. {done} fetched, {cached} cached, stopped at {blocked_at}.")
        say("Wait 30–60 minutes (longer if it recurs), then re-run the same command: "
            "cached videos are skipped automatically.")
        say("If it keeps happening: run from a home/office IP rather than a cloud box, "
            "add --cookies-from-browser <browser>, and/or "
            "--extractor-args 'youtube:player_client=tv,web_safari'. See README → Rate limits.")
        return EXIT_BLOCKED

    say(f"\n{C['bold']}{C['green']}━━━ Done ━━━{C['reset']}")
    say(f"{done} fetched, {cached} cached, {failed} failed.")
    say(f"Output: {C['cyan']}{root}{C['reset']}")
    say(f"{C['dim']}Open {root}/00_INDEX.md for paste instructions.{C['reset']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
