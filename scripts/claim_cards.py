#!/usr/bin/env python3
"""
claim_cards.py — the checkable claims in a video, as cards
===========================================================
A video usually contains a few ideas someone could act on or check,
buried in talk. This module stores each one as a *claim card* (what is
claimed, the creator's own words, where they said them, plus a few
subject-specific fields), groups cards across creators, and — for
subjects that have one — records how a card translated into something a
tool can test.

It works for any subject. What a card looks like is set by a **profile**,
a small JSON file under `profiles/`:

    generic.json   rules, techniques, recommendations, predictions …
    trading.json   strategies (instrument, entry, exit, sizing …), with a
                   translation step into the option-backtesting DSL

Add your own by copying one: `name`, `card_is` (what counts as a card),
`tags` (coarse categories), `fields` (what to capture; null when the
creator did not say), `cluster_by` (the field cards are grouped on), and
optionally `translation`.

It does no model work itself. Cards are written by a Claude session
through the MCP server, following prompts/02_claim_cards.md. What this
module adds is the checking:

    * `ts` must be a `[MM:SS]` marker of the transcript;
    * `quote` must appear in the transcript word for word;
    * anything the creator did not state stays null — and a translation
      must list every part it could not express, or had to choose, under
      `manual_review` instead of filling it in.

Files:
    distilled/<playlist>/claim_cards/<profile>/<video_NN>.json
    distilled/claim_cards.md           (all playlists, clustered)

USAGE:
    python scripts/claim_cards.py            # rebuild distilled/claim_cards.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from knowledge_diff import deep_link
from preprocess_transcript import MARKER_RE, parse_ts

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"
DEFAULT_PROFILE = "generic"
MIN_QUOTE_WORDS = 5
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_RESERVED = {"id", "claim", "tag", "quote", "ts", "link", "translation"}
_RENDER_SKIP = _RESERVED | {"creator", "playlist", "video", "title"}


class CardError(ValueError):
    """Cards, a profile or a translation that fail the checks; nothing is written."""


# ── Profiles ─────────────────────────────────────────────────────────────────
def profile_names() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json") if _NAME_RE.match(p.stem))


def load_profile(name: str) -> dict:
    """A validated profile, by name (allow-listed before any path is built)."""
    if name not in profile_names():
        raise CardError(f"Unknown profile {name!r}. Available: {', '.join(profile_names())}")
    try:
        profile = json.loads((PROFILES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise CardError(f"Profile {name!r} is not readable JSON: {e}") from e
    tags, fields = profile.get("tags"), profile.get("fields")
    field_names = [f.get("name") for f in fields] if isinstance(fields, list) else None
    ok = (
        isinstance(tags, list) and tags and all(isinstance(t, str) and t for t in tags)
        and field_names is not None
        and all(isinstance(n, str) and re.fullmatch(r"[a-z][a-z0-9_]*", n) and n not in _RENDER_SKIP
                for n in field_names)
        and len(set(field_names)) == len(field_names)
        and isinstance(profile.get("card_is"), str)
        and profile.get("cluster_by") in [None, *field_names]
        and set((profile.get("translation") or {}).get("must_state", [])) <= set(field_names)
    )
    if not ok:
        raise CardError(f"Profile {name!r} is malformed: it needs card_is, non-empty tags, uniquely named "
                        "fields, and cluster_by / translation.must_state naming its own fields.")
    profile["name"] = name
    return profile


def load_profile_safely(name: str) -> dict | None:
    """None when the profile file has gone since its cards were saved."""
    try:
        return load_profile(name)
    except CardError:
        return None


def instructions(profile: dict) -> str:
    """The profile, rendered as the second half of the extraction prompt."""
    lines = [f"## Profile: {profile['name']}", "",
             f"A card is {profile['card_is']}", "",
             "Tags: " + ", ".join(f"`{t}`" for t in profile["tags"]) + ".", "",
             "Fields (each is the stated text, or `null` when the creator did not say):"]
    lines += [f"- `{f['name']}` — {f.get('description', '')}" for f in profile["fields"]]
    return "\n".join(lines)


# ── Checking ─────────────────────────────────────────────────────────────────
def _norm(text: str) -> str:
    """Lower-case, markers and punctuation removed, whitespace collapsed."""
    text = MARKER_RE.sub(" ", text).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def check_cards(cards: list, transcript_text: str, profile: dict) -> list[dict]:
    """Validated, id-stamped copies of `cards`, or CardError listing every problem."""
    if not isinstance(cards, list):
        raise CardError("cards must be a list (empty when the video contains none).")
    markers = set(MARKER_RE.findall(transcript_text))
    haystack = _norm(transcript_text)
    field_names = [f["name"] for f in profile["fields"]]
    problems: list[str] = []
    out: list[dict] = []
    for idx, card in enumerate(cards):
        where = f"card {idx}"
        if not isinstance(card, dict):
            problems.append(f"{where}: must be an object")
            continue
        claim = str(card.get("claim") or "").strip()
        if not claim:
            problems.append(f"{where}: claim is required")
        tag = card.get("tag")
        if tag not in profile["tags"]:
            problems.append(f"{where}: tag must be one of {', '.join(profile['tags'])}")
        quote = str(card.get("quote") or "").strip()
        if len(quote.split()) < MIN_QUOTE_WORDS:
            problems.append(f"{where}: quote must be at least {MIN_QUOTE_WORDS} words, copied from the transcript")
        elif _norm(quote) not in haystack:
            problems.append(f"{where}: quote is not in the transcript word for word")
        ts = str(card.get("ts") or "").strip().strip("[]")
        if markers and ts not in markers:
            problems.append(f"{where}: ts {ts!r} is not a [MM:SS] marker of this transcript")
        unknown = sorted(set(card) - _RESERVED - set(field_names))
        if unknown:
            problems.append(f"{where}: fields not in the {profile['name']} profile: {', '.join(unknown)}")
        clean = {"id": f"c{idx}", "claim": claim, "tag": tag, "quote": quote, "ts": ts}
        for field in field_names:
            value = card.get(field)
            if value is not None and not isinstance(value, str):
                problems.append(f"{where}: {field} must be text, or null when the creator did not state it")
                continue
            clean[field] = value.strip() if isinstance(value, str) and value.strip() else None
        out.append(clean)
    if problems:
        raise CardError("; ".join(problems))
    return out


# ── Files ────────────────────────────────────────────────────────────────────
def cards_path(distilled_root: Path, playlist: str, profile_name: str, video: str) -> Path:
    return distilled_root / playlist / "claim_cards" / profile_name / f"{video}.json"


def save_cards(distilled_root: Path, playlist: str, video: str, cards: list,
               transcript_text: str, meta: dict, profile: dict) -> dict:
    """Check and write one video's cards (replacing any earlier ones), refresh the index."""
    checked = check_cards(cards, transcript_text, profile)
    for card in checked:
        seconds = parse_ts(card["ts"])
        card["link"] = deep_link(meta.get("url"), seconds) if seconds is not None else None
    record = {
        "profile": profile["name"],
        "playlist": playlist,
        "video": video,
        "title": meta.get("title"),
        "url": meta.get("url"),
        "creator": meta.get("uploader") or playlist,
        "timestamps_checked": bool(MARKER_RE.search(transcript_text)),
        "cards": checked,
    }
    path = cards_path(distilled_root, playlist, profile["name"], video)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    write_index(distilled_root)
    return record


def load_all(distilled_root: Path, playlist: str | None = None, profile_name: str | None = None) -> list[dict]:
    """Every saved card record, optionally narrowed to a playlist and/or profile."""
    records: list[dict] = []
    if not distilled_root.is_dir():
        return records
    pattern = f"{playlist or '*'}/claim_cards/{profile_name or '*'}/video_*.json"
    for path in sorted(distilled_root.glob(pattern)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("cards"), list) and data.get("profile"):
            records.append(data)
    return records


def save_translation(distilled_root: Path, playlist: str, video: str, card_id: str, profile: dict, *,
                     artifact: str | None, valid: bool, validation_errors: list[str],
                     manual_review: list[str]) -> dict:
    """Attach a testable translation to one card, for profiles that define one.

    `valid` / `validation_errors` are what the external validator returned —
    this module cannot check them itself and records them as reported.
    `manual_review` lists every part of the card the target could not
    express; a card with no artifact must say why there.
    """
    spec = profile.get("translation")
    if not spec:
        raise CardError(f"The {profile['name']} profile has no translation step.")
    path = cards_path(distilled_root, playlist, profile["name"], video)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise CardError(f"{video} has no saved {profile['name']} cards.") from e
    card = next((c for c in record["cards"] if c.get("id") == card_id), None)
    if card is None:
        raise CardError(f"No card {card_id!r} in {video}. Ids: {', '.join(c['id'] for c in record['cards'])}")
    if not isinstance(manual_review, list) or any(not isinstance(m, str) or not m.strip() for m in manual_review):
        raise CardError("manual_review must be a list of non-empty strings (empty only when nothing was left out).")
    if not isinstance(validation_errors, list):
        raise CardError("validation_errors must be a list.")
    text = (artifact or "").strip() or None
    if text is None and not manual_review:
        raise CardError("With no artifact, manual_review must say what could not be translated.")
    if text is None and valid:
        raise CardError("valid cannot be true without an artifact.")
    if valid and validation_errors:
        raise CardError("valid cannot be true while validation_errors is non-empty.")
    unstated = [f for f in spec.get("must_state", []) if card.get(f) is None]
    unflagged = [f for f in unstated if not any(f in m.lower() for m in manual_review)]
    if text is not None and unflagged:
        raise CardError(
            "The creator did not state: " + ", ".join(unflagged) + ". The translation needs them, so whatever "
            "it uses was your choice — name each in manual_review (e.g. '" + unflagged[0] + ": not stated, used …')."
        )
    card["translation"] = {
        "artifact_kind": spec.get("artifact", "artifact"),
        "artifact": text,
        "valid": bool(valid),
        "validation_errors": [str(e) for e in validation_errors],
        "manual_review": [m.strip() for m in manual_review],
        "validated_by": "external validator, as reported by the translating session",
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    write_index(distilled_root)
    return card


# ── Clustering + rendering ───────────────────────────────────────────────────
def _key(value: str | None) -> str:
    return re.sub(r"[\W_]+", " ", (value or "").lower()).strip() or "unspecified"


def cluster(records: list[dict]) -> list[dict]:
    """Group cards by (profile, cluster_by field, tag); most creators first."""
    groups: dict[tuple[str, str, str], list[dict]] = {}
    fields: dict[str, str | None] = {}
    for rec in records:
        name = rec["profile"]
        if name not in fields:
            fields[name] = (load_profile_safely(name) or {}).get("cluster_by")
        for card in rec["cards"]:
            subject = _key(card.get(fields[name])) if fields[name] else "all"
            groups.setdefault((name, subject, card["tag"]), []).append({
                **card, "creator": rec.get("creator"), "playlist": rec["playlist"],
                "video": rec["video"], "title": rec.get("title"),
            })
    out = [{
        "profile": name, "subject": subject, "tag": tag, "cards": cards,
        "creators": sorted({c["creator"] or c["playlist"] for c in cards}),
    } for (name, subject, tag), cards in groups.items()]
    out.sort(key=lambda g: (g["profile"], -len(g["creators"]), -len(g["cards"]), g["subject"], g["tag"]))
    return out


def _translation_line(card: dict) -> str:
    tr = card.get("translation")
    if not tr:
        return "not translated yet"
    if tr["artifact"] is None:
        state = "could not be translated"
    else:
        state = "translated, validates" if tr["valid"] else "translated, does NOT validate"
    review = f"; manual review: {'; '.join(tr['manual_review'])}" if tr["manual_review"] else ""
    return state + review


def render_index(records: list[dict]) -> str:
    groups = cluster(records)
    n_cards = sum(len(g["cards"]) for g in groups)
    lines = ["# Claim cards", "",
             f"{n_cards} card(s) from {len(records)} video(s), in {len(groups)} cluster(s). "
             "These are creators' claims, not tested results.", ""]
    for g in groups:
        translatable = bool((load_profile_safely(g["profile"]) or {}).get("translation"))
        lines += [f"## [{g['profile']}] {g['subject']} — {g['tag']} "
                  f"({len(g['cards'])} card(s), {len(g['creators'])} creator(s): {', '.join(g['creators'])})", ""]
        for card in g["cards"]:
            where = f"[{card['ts']}]({card['link']})" if card.get("link") else (card["ts"] or "--:--")
            lines.append(f"### {card['claim']}")
            lines.append(f"{card['creator']} — {card.get('title') or card['video']} at {where} "
                         f"(`{card['playlist']}/{card['video']}#{card['id']}`)")
            lines += ["", f"> {card['quote']}", ""]
            for field in (k for k in card if k not in _RENDER_SKIP):
                lines.append(f"- **{field}:** {card[field] if card.get(field) else '_not stated_'}")
            if translatable or "translation" in card:
                lines.append(f"- **testable form:** {_translation_line(card)}")
            lines.append("")
    return "\n".join(lines)


def write_index(distilled_root: Path) -> Path:
    path = distilled_root / "claim_cards.md"
    path.write_text(render_index(load_all(distilled_root)), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Rebuild the clustered claim-card index.")
    p.add_argument("--distilled-root", default="distilled")
    args = p.parse_args(argv)
    root = Path(args.distilled_root)
    records = load_all(root)
    if not records:
        print("No claim cards yet. They are written through the MCP server "
              "(save_claim_cards); see README 'Claim cards'.", file=sys.stderr)
        return 1
    print(f"Wrote {write_index(root)} ({sum(len(r['cards']) for r in records)} cards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
