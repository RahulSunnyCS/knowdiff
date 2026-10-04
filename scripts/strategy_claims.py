#!/usr/bin/env python3
"""
strategy_claims.py — hypothesis cards for trading content
==========================================================
A trading video usually contains a few testable ideas buried in talk. This
module stores each one as a *hypothesis card* (instrument, entry, exit,
sizing, stated regime, the creator's own words and where they said them),
groups cards across creators, and records how each card translated into a
backtestable strategy.

It does no model work itself. The cards are written by a Claude session
following prompts/02_strategy_claims.md (through the MCP server), and the
translation is done by that same session using the option-backtesting MCP
server's `validate_strategy` / `propose_strategy`, following
prompts/07_strategy_translate.md. What this module adds is the checking:

    * `ts` must be a `[MM:SS]` marker of the transcript;
    * `quote` must appear in the transcript word for word;
    * anything the creator did not state stays null — and a translation
      must list every part it could not express under `manual_review`
      instead of filling it in.

Files:
    distilled/<playlist>/strategy_cards/<video_NN>.json
    distilled/strategy_cards.md        (all playlists, clustered)

USAGE:
    python scripts/strategy_claims.py            # rebuild distilled/strategy_cards.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from knowledge_diff import deep_link
from preprocess_transcript import MARKER_RE, parse_ts

# Coarse structure vocabulary, so cards from different creators cluster
# without anyone having to agree on wording.
STRUCTURE_TAGS = (
    "short_straddle", "short_strangle", "long_straddle", "long_strangle",
    "iron_condor", "iron_fly", "credit_spread", "debit_spread", "calendar",
    "option_buy", "option_sell", "futures", "equity", "other",
)
# Free-text fields a creator may or may not have stated (null when not).
OPTIONAL_FIELDS = ("instrument", "entry", "exit", "sizing", "regime", "edge")
MIN_QUOTE_WORDS = 5


class CardError(ValueError):
    """Cards or a translation that fail the checks; nothing is written."""


def _norm(text: str) -> str:
    """Lower-case, markers and punctuation removed, whitespace collapsed."""
    text = MARKER_RE.sub(" ", text).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text)).strip()


def check_cards(cards: list, transcript_text: str) -> list[dict]:
    """Validated, id-stamped copies of `cards`, or CardError listing every problem."""
    if not isinstance(cards, list):
        raise CardError("cards must be a list (empty when the video states no testable strategy).")
    markers = set(MARKER_RE.findall(transcript_text))
    haystack = _norm(transcript_text)
    problems: list[str] = []
    out: list[dict] = []
    for idx, card in enumerate(cards):
        where = f"card {idx}"
        if not isinstance(card, dict):
            problems.append(f"{where}: must be an object")
            continue
        hypothesis = str(card.get("hypothesis") or "").strip()
        if not hypothesis:
            problems.append(f"{where}: hypothesis is required")
        tag = card.get("structure")
        if tag not in STRUCTURE_TAGS:
            problems.append(f"{where}: structure must be one of {', '.join(STRUCTURE_TAGS)}")
        quote = str(card.get("quote") or "").strip()
        if len(quote.split()) < MIN_QUOTE_WORDS:
            problems.append(f"{where}: quote must be at least {MIN_QUOTE_WORDS} words, copied from the transcript")
        elif _norm(quote) not in haystack:
            problems.append(f"{where}: quote is not in the transcript word for word")
        ts = str(card.get("ts") or "").strip().strip("[]")
        if markers and ts not in markers:
            problems.append(f"{where}: ts {ts!r} is not a [MM:SS] marker of this transcript")
        clean = {"id": f"c{idx}", "hypothesis": hypothesis, "structure": tag, "quote": quote, "ts": ts}
        for field in OPTIONAL_FIELDS:
            value = card.get(field)
            if value is not None and not isinstance(value, str):
                problems.append(f"{where}: {field} must be text, or null when the creator did not state it")
                continue
            clean[field] = value.strip() if isinstance(value, str) and value.strip() else None
        out.append(clean)
    if problems:
        raise CardError("; ".join(problems))
    return out


def cards_path(distilled_root: Path, playlist: str, video: str) -> Path:
    return distilled_root / playlist / "strategy_cards" / f"{video}.json"


def save_cards(distilled_root: Path, playlist: str, video: str, cards: list,
               transcript_text: str, meta: dict) -> dict:
    """Check and write one video's cards (replacing any earlier ones), refresh the index."""
    checked = check_cards(cards, transcript_text)
    for card in checked:
        seconds = parse_ts(card["ts"])
        card["link"] = deep_link(meta.get("url"), seconds) if seconds is not None else None
    record = {
        "playlist": playlist,
        "video": video,
        "title": meta.get("title"),
        "url": meta.get("url"),
        "creator": meta.get("uploader") or playlist,
        "timestamps_checked": bool(MARKER_RE.search(transcript_text)),
        "cards": checked,
    }
    path = cards_path(distilled_root, playlist, video)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    write_index(distilled_root)
    return record


def load_all(distilled_root: Path, playlist: str | None = None) -> list[dict]:
    """Every saved card record, optionally for one playlist."""
    records: list[dict] = []
    if not distilled_root.is_dir():
        return records
    pattern = f"{playlist}/strategy_cards/video_*.json" if playlist else "*/strategy_cards/video_*.json"
    for path in sorted(distilled_root.glob(pattern)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("cards"), list):
            records.append(data)
    return records


def save_translation(distilled_root: Path, playlist: str, video: str, card_id: str, *,
                     strategy_yaml: str | None, valid: bool, validation_errors: list[str],
                     manual_review: list[str]) -> dict:
    """Attach a DSL translation to one card.

    `valid` / `validation_errors` are what the option-backtesting server's
    validate_strategy returned — this module cannot check them itself and
    records them as reported. `manual_review` lists every part of the card
    the DSL could not express; a card with no YAML must say why there.
    """
    path = cards_path(distilled_root, playlist, video)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise CardError(f"{video} has no saved strategy cards.") from e
    card = next((c for c in record["cards"] if c.get("id") == card_id), None)
    if card is None:
        raise CardError(f"No card {card_id!r} in {video}. Ids: {', '.join(c['id'] for c in record['cards'])}")
    if not isinstance(manual_review, list) or any(not isinstance(m, str) or not m.strip() for m in manual_review):
        raise CardError("manual_review must be a list of non-empty strings (empty only when nothing was left out).")
    if not isinstance(validation_errors, list):
        raise CardError("validation_errors must be a list.")
    yaml_text = (strategy_yaml or "").strip() or None
    if yaml_text is None and not manual_review:
        raise CardError("With no strategy_yaml, manual_review must say what could not be translated.")
    if yaml_text is None and valid:
        raise CardError("valid cannot be true without strategy_yaml.")
    if valid and validation_errors:
        raise CardError("valid cannot be true while validation_errors is non-empty.")
    unstated = [f for f in ("entry", "exit", "sizing") if card.get(f) is None]
    unflagged = [f for f in unstated if not any(f in m.lower() for m in manual_review)]
    if yaml_text is not None and unflagged:
        raise CardError(
            "The creator did not state: " + ", ".join(unflagged) + ". A strategy needs them, so whatever "
            "the YAML uses was your choice — name each in manual_review (e.g. 'exit: not stated, used 15:15 time exit')."
        )
    card["translation"] = {
        "strategy_yaml": yaml_text,
        "valid": bool(valid),
        "validation_errors": [str(e) for e in validation_errors],
        "manual_review": [m.strip() for m in manual_review],
        "validated_by": "option-backtesting validate_strategy (as reported by the translating session)",
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    write_index(distilled_root)
    return card


# ── Clustering + rendering ───────────────────────────────────────────────────
def _instrument_key(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (value or "").upper()) or "UNSPECIFIED"


def cluster(records: list[dict]) -> list[dict]:
    """Group cards by (instrument, structure); most creators first."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for rec in records:
        for card in rec["cards"]:
            key = (_instrument_key(card.get("instrument")), card["structure"])
            groups.setdefault(key, []).append({
                **card, "creator": rec.get("creator"), "playlist": rec["playlist"],
                "video": rec["video"], "title": rec.get("title"),
            })
    out = [{
        "instrument": inst, "structure": tag, "cards": cards,
        "creators": sorted({c["creator"] or c["playlist"] for c in cards}),
    } for (inst, tag), cards in groups.items()]
    out.sort(key=lambda g: (-len(g["creators"]), -len(g["cards"]), g["instrument"], g["structure"]))
    return out


def _translation_line(card: dict) -> str:
    tr = card.get("translation")
    if not tr:
        return "not translated yet"
    if tr["strategy_yaml"] is None:
        state = "could not be translated"
    else:
        state = "translated, validates" if tr["valid"] else "translated, does NOT validate"
    review = f"; manual review: {'; '.join(tr['manual_review'])}" if tr["manual_review"] else ""
    return state + review


def render_index(records: list[dict]) -> str:
    groups = cluster(records)
    n_cards = sum(len(g["cards"]) for g in groups)
    lines = ["# Strategy hypothesis cards", "",
             f"{n_cards} card(s) from {len(records)} video(s), in {len(groups)} cluster(s). "
             "These are creators' claims, not tested results.", ""]
    for g in groups:
        lines += [f"## {g['instrument']} — {g['structure']} "
                  f"({len(g['cards'])} card(s), {len(g['creators'])} creator(s): {', '.join(g['creators'])})", ""]
        for card in g["cards"]:
            where = f"[{card['ts']}]({card['link']})" if card.get("link") else (card["ts"] or "--:--")
            lines.append(f"### {card['hypothesis']}")
            lines.append(f"{card['creator']} — {card.get('title') or card['video']} at {where} "
                         f"(`{card['playlist']}/{card['video']}#{card['id']}`)")
            lines.append("")
            lines.append(f"> {card['quote']}")
            lines.append("")
            for field in OPTIONAL_FIELDS[1:]:
                lines.append(f"- **{field}:** {card[field] if card.get(field) else '_not stated_'}")
            lines.append(f"- **backtest:** {_translation_line(card)}")
            lines.append("")
    return "\n".join(lines)


def write_index(distilled_root: Path) -> Path:
    path = distilled_root / "strategy_cards.md"
    path.write_text(render_index(load_all(distilled_root)), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Rebuild the clustered strategy-card index.")
    p.add_argument("--distilled-root", default="distilled")
    args = p.parse_args(argv)
    root = Path(args.distilled_root)
    records = load_all(root)
    if not records:
        print("No strategy cards yet. They are written through the MCP server "
              "(save_strategy_cards); see README 'Strategy claims'.", file=sys.stderr)
        return 1
    print(f"Wrote {write_index(root)} ({sum(len(r['cards']) for r in records)} cards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
