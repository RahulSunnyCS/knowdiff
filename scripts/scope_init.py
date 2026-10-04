"""
Phase 0: interactive scoping CLI.

Walks the user through intent selection, language, depth, themes,
question, audience, rights confirmation, and model picks. Emits
`scope.json` and `consent.json` under distilled/<playlist>/.

Usage:
    python scripts/scope_init.py --playlist <name>

Non-interactive (useful for CI/tests): pass --non-interactive plus the
specific flags you want set.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import scope as scope_module
from scope import DEFAULT_MODELS, VALID_INTENTS, Scope


def _prompt(label: str, default: str = "", allowed: list[str] | None = None) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        choices = f" ({'/'.join(allowed)})" if allowed else ""
        raw = input(f"{label}{choices}{suffix}: ").strip()
        if not raw and default:
            raw = default
        if allowed and raw not in allowed:
            print(f"  please pick one of: {', '.join(allowed)}")
            continue
        return raw


def _prompt_yes_no(label: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    raw = input(f"{label} [{d}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def _prompt_list(label: str) -> list[str]:
    raw = input(f"{label} (comma-separated, blank for none): ").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def interactive(playlist: str) -> tuple[Scope, dict]:
    print(f"\n=== Phase 0 scoping for playlist '{playlist}' ===\n")

    print("What kind of output do you want?")
    print("  method-distillation  - a Claude Skill that thinks like the creator")
    print("  style-clone          - a Claude Skill that mimics their phrasing")
    print("  summary              - per-video + playlist summary")
    print("  stats                - word/phrase frequency (no Claude cost)")
    print("  quote-mining         - extract verbatim quotes by theme (no Claude cost)")
    print("  topical-report       - PDF answering 'what does X think about Y?'")
    print("  knowledge-diff       - watch-list of only the minutes that are new to you")
    print("  strategy-claims      - testable trading ideas as hypothesis cards (via the MCP server)")
    intent = _prompt("Intent", default="method-distillation", allowed=sorted(VALID_INTENTS))

    language = _prompt("Source language (ISO 639-1, or 'auto')", default="auto")
    depth = _prompt("Depth", default="standard", allowed=["quick", "standard", "deep"])

    themes: list[str] = []
    question = ""
    if intent in {"quote-mining"}:
        themes = _prompt_list("Themes to search for")
        while not themes:
            print("  quote-mining needs at least one theme.")
            themes = _prompt_list("Themes to search for")
    if intent == "topical-report":
        question = _prompt("Your question (e.g. 'what does X say about commodities?')")
        while not question:
            question = _prompt("Question (required for topical-report)")

    audience = _prompt(
        "Target audience (personal=just for you, shared=plan to publish the output)",
        default="personal", allowed=["personal", "shared"],
    )

    print("\nModel choices (defaults are the cost-optimised picks):")
    m2 = _prompt("  Phase 2 model", default=DEFAULT_MODELS["phase2"])
    m3 = _prompt("  Phase 3 model", default=DEFAULT_MODELS["phase3"])
    m4 = _prompt("  Phase 4 model", default=DEFAULT_MODELS["phase4"])

    print("\n=== Rights confirmation ===")
    print("This tool only runs on content you have the right to use:")
    print("  - your own videos")
    print("  - Creative Commons or openly-licensed content")
    print("  - content the creator has explicitly authorized you to use")
    confirmed = _prompt_yes_no("Confirm you have rights to this content", default=False)
    if not confirmed:
        print("\nAborted. No scope.json written. The tool refuses to proceed "
              "without rights confirmation.")
        sys.exit(2)

    consent = {
        "playlist": playlist,
        "confirmed": True,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "audience": audience,
        "rights_statement": (
            "Operator confirms they own the source content, it is "
            "Creative Commons / openly-licensed, or they have the "
            "creator's explicit authorization to use it."
        ),
    }

    scope = Scope(
        intent=intent,
        language=language,
        depth=depth,
        themes=themes,
        question=question,
        target_audience=audience,
        models={"phase2": m2, "phase3": m3, "phase4": m4},
    )
    return scope, consent


def estimate_cost_quick(intent: str, n_videos: int = 40) -> str:
    """Rough order-of-magnitude estimate string. Honest about uncertainty."""
    if intent == "stats":
        return "≈ $0 (no Claude calls)"
    if intent == "quote-mining":
        return "≈ $0 (no Claude calls by default)"
    if intent in {"method-distillation", "style-clone"}:
        return f"≈ ${0.005 * n_videos:.2f} (Haiku Phase 2 + Sonnet Phase 3/4)"
    if intent == "summary":
        return f"≈ ${0.003 * n_videos:.2f} (Haiku per-video summary + Sonnet rollup)"
    if intent == "topical-report":
        return f"≈ ${0.004 * n_videos:.2f} (targeted extraction + Sonnet report)"
    return "unknown"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--playlist", required=True)
    p.add_argument("--distilled-root", default="distilled")
    p.add_argument("--non-interactive", action="store_true")
    p.add_argument("--intent", default="method-distillation")
    p.add_argument("--language", default="auto")
    p.add_argument("--depth", default="standard")
    p.add_argument("--themes", default="")
    p.add_argument("--question", default="")
    p.add_argument("--audience", default="personal")
    p.add_argument("--assume-rights", action="store_true",
                   help="In --non-interactive, skip the rights prompt (assume yes).")
    p.add_argument("--n-videos-estimate", type=int, default=40,
                   help="Used only for the cost-estimate message")
    args = p.parse_args()

    distilled_root = Path(args.distilled_root)

    if args.non_interactive:
        scope = Scope(
            intent=args.intent,
            language=args.language,
            depth=args.depth,
            themes=[t.strip() for t in args.themes.split(",") if t.strip()],
            question=args.question,
            target_audience=args.audience,
        )
        if not args.assume_rights:
            sys.exit("--non-interactive requires --assume-rights to record consent.")
        consent = {
            "playlist": args.playlist,
            "confirmed": True,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "audience": args.audience,
            "rights_statement": "Operator confirmed rights via --assume-rights flag.",
        }
    else:
        scope, consent = interactive(args.playlist)

    print(f"\nEstimated Claude cost (assumes ~{args.n_videos_estimate} videos): "
          f"{estimate_cost_quick(scope.intent, args.n_videos_estimate)}\n")

    distilled_dir = distilled_root / args.playlist
    distilled_dir.mkdir(parents=True, exist_ok=True)
    scope_path = scope_module.save(distilled_root, args.playlist, scope)
    consent_path = distilled_dir / "consent.json"
    consent_path.write_text(json.dumps(consent, indent=2))
    print(f"Wrote {scope_path}")
    print(f"Wrote {consent_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
