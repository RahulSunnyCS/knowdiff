"""
Read/write scope.json — the Phase 0 configuration that downstream phases
consult for intent, language, depth, model choice, and themes.

scope.json lives at distilled/<playlist>/scope.json.

Default models per phase: Haiku for the mechanical Phase 2 extraction,
Sonnet for Phase 3 synthesis and Phase 4 authoring.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


# Haiku for the mechanical per-video extraction (cheapest, good enough for
# schema-constrained JSON); the current Sonnet for synthesis and authoring.
# Use the bare IDs — no date suffix — so the SDK resolves the served model.
DEFAULT_MODELS = {
    "phase2": "claude-haiku-4-5",
    "phase3": "claude-sonnet-5-5",
    "phase4": "claude-sonnet-5-5",
}

VALID_INTENTS = {
    "method-distillation",
    "style-clone",
    "summary",
    "stats",
    "quote-mining",
    "topical-report",
    "knowledge-diff",
    "strategy-claims",
}


@dataclass
class Scope:
    intent: str = "method-distillation"
    language: str = "auto"
    depth: str = "standard"
    themes: list[str] = field(default_factory=list)
    question: str = ""
    target_audience: str = "personal"
    models: dict = field(default_factory=lambda: dict(DEFAULT_MODELS))

    def validate(self) -> None:
        if self.intent not in VALID_INTENTS:
            raise ValueError(f"intent must be one of {sorted(VALID_INTENTS)}; got {self.intent!r}")
        if self.depth not in {"quick", "standard", "deep"}:
            raise ValueError(f"depth must be quick/standard/deep; got {self.depth!r}")
        if self.intent in {"quote-mining"} and not self.themes:
            raise ValueError("intent=quote-mining requires non-empty themes")
        if self.intent in {"topical-report"} and not self.question:
            raise ValueError("intent=topical-report requires question")

    def model_for(self, phase: str) -> str:
        return self.models.get(phase, DEFAULT_MODELS.get(phase, DEFAULT_MODELS["phase2"]))


def scope_path(distilled_root: Path, playlist: str) -> Path:
    return distilled_root / playlist / "scope.json"


def load(distilled_root: Path, playlist: str) -> Scope:
    p = scope_path(distilled_root, playlist)
    if not p.exists():
        scope = Scope()
        scope.validate()
        return scope
    data = json.loads(p.read_text())
    models = {**DEFAULT_MODELS, **data.get("models", {})}
    scope = Scope(
        intent=data.get("intent", "method-distillation"),
        language=data.get("language", "auto"),
        depth=data.get("depth", "standard"),
        themes=list(data.get("themes", [])),
        question=data.get("question", ""),
        target_audience=data.get("target_audience", "personal"),
        models=models,
    )
    scope.validate()
    return scope


def save(distilled_root: Path, playlist: str, scope: Scope) -> Path:
    scope.validate()
    p = scope_path(distilled_root, playlist)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(scope), indent=2))
    return p
