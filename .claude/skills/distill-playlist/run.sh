#!/usr/bin/env bash
# Run Phase 2 + Phase 3 + Phase 4 in sequence for a youtube-skill-fetch playlist.
# Stops on the first non-zero exit so partial state is visible for review.

set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "Usage: $0 <PLAYLIST_NAME> [SKILL_MODE=Teacher] [OUT=output]" >&2
  exit 2
fi

PLAYLIST_NAME="$1"
SKILL_MODE="${2:-Teacher}"
OUT="${3:-output}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

SCOPE="distilled/${PLAYLIST_NAME}/scope.json"
if [[ ! -f "$SCOPE" ]]; then
  echo "ERROR: $SCOPE not found. Run 'make scope PLAYLIST_NAME=$PLAYLIST_NAME' first." >&2
  exit 1
fi

if ! ls "${OUT}/${PLAYLIST_NAME}"/video_*/transcript.clean.txt >/dev/null 2>&1; then
  echo "ERROR: no transcript.clean.txt found under ${OUT}/${PLAYLIST_NAME}/." >&2
  echo "Run 'make preprocess PLAYLIST_NAME=${PLAYLIST_NAME}' first." >&2
  exit 1
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ERROR: ANTHROPIC_API_KEY is not set." >&2
  exit 1
fi

case "$SKILL_MODE" in
  Teacher|Reviewer|Advisor) ;;
  *)
    echo "ERROR: SKILL_MODE must be Teacher, Reviewer, or Advisor (got: $SKILL_MODE)." >&2
    exit 2
    ;;
esac

echo "==> Phase 2: per-video distillation (playlist=${PLAYLIST_NAME})"
python3 scripts/run_phase2.py --playlist "$PLAYLIST_NAME" --output-root "$OUT"

echo "==> Phase 3: cross-video synthesis"
python3 scripts/run_phase3.py --playlist "$PLAYLIST_NAME"

echo "==> Phase 4: author SKILL.md (mode=${SKILL_MODE})"
python3 scripts/run_phase4.py --playlist "$PLAYLIST_NAME" --mode "$SKILL_MODE"

COST_FILE="distilled/${PLAYLIST_NAME}/cost.json"
echo
echo "Done. Artifacts in distilled/${PLAYLIST_NAME}/:"
echo "  - synthesis.json"
echo "  - SKILL.md"
echo "  - citations.md"
echo "  - CHANGELOG.md"
if [[ -f "$COST_FILE" ]]; then
  echo "  - cost.json"
  echo
  python3 -c "import json,sys; d=json.load(open('${COST_FILE}')); print(f'Total estimated cost: \${d.get(\"total_estimated_cost_usd\", 0):.4f}')"
fi
