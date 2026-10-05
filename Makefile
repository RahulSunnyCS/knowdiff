PLAYLIST ?= https://www.youtube.com/playlist?list=PLS2yrp7IIPNj-GKemcb5p8IrIA7EUwvTE
PLAYLIST_NAME ?= playlist
MODE ?= talking-head
OUT ?= output
SKILL_MODE ?= Teacher
# Optional: '10-25', '1,3,5-7', or a single number. Empty = all.
VIDEOS ?=
# Parallel video workers for the extract step. Each worker is paced, so N
# workers send N times the requests YouTube sees; keep at 1 unless 1 is
# never blocked, and at 1 for --force-whisper / screen-heavy.
JOBS ?= 1

EXTRACT_FLAGS = --mode $(MODE) --out $(OUT) --jobs $(JOBS)
ifneq ($(strip $(VIDEOS)),)
EXTRACT_FLAGS += --videos "$(VIDEOS)"
endif

.PHONY: help scope test1 extract extract-batch preprocess phase2 phase3 phase4 \
        topical summary stats quote-mine screenshots citations \
        diff-synthesis eval knowledge-diff test mcp mcp-http clean

help:
	@echo "Setup:"
	@echo "  make scope PLAYLIST_NAME=...           - interactive Phase 0 scoping"
	@echo ""
	@echo "Extract + prep (local, free):"
	@echo "  make test1                              - extract one video as sanity check"
	@echo "  make extract                            - interactive extract (prompts for url, range, jobs)"
	@echo "  make extract-batch PLAYLIST=... PLAYLIST_NAME=... VIDEOS=10-25   - non-interactive"
	@echo "  make preprocess PLAYLIST_NAME=...       - clean transcripts"
	@echo "  make screenshots PLAYLIST_NAME=...      - frames at deictic moments"
	@echo ""
	@echo "Claude phases (cost ≈ playlist length):"
	@echo "  make phase2 PLAYLIST_NAME=...           - per-video distillation"
	@echo "  make phase3 PLAYLIST_NAME=...           - cross-video synthesis"
	@echo "  make phase4 PLAYLIST_NAME=... SKILL_MODE=Teacher  - author SKILL.md"
	@echo ""
	@echo "Alternative intents:"
	@echo "  make topical PLAYLIST_NAME=...          - topical report (PDF if pandoc)"
	@echo "  make summary PLAYLIST_NAME=...          - per-video + playlist summary"
	@echo "  make stats PLAYLIST_NAME=...            - local word/topic stats (\$$0)"
	@echo "  make quote-mine PLAYLIST_NAME=... THEMES='a,b,c'  - quotes (\$$0)"
	@echo "  make knowledge-diff PLAYLIST_NAME=...   - watch-list of what is new to you (\$$0, word-overlap judge)"
	@echo ""
	@echo "Audit + iterate:"
	@echo "  make citations PLAYLIST_NAME=...        - regenerate citations sidecar"
	@echo "  make diff-synthesis OLD=... NEW=...     - compare two synthesis.json"
	@echo "  make eval PLAYLIST_NAME=...             - hold-one-out scoring (leak-free rebuild)"
	@echo "  make test                               - run the unit tests (no network, no API key)"
	@echo "  make mcp                                - MCP server over stdio (no API key)"
	@echo "  make mcp-http MCP_HOST=<tunnel host>    - MCP server over HTTP for a tunnel (needs KNOWDIFF_MCP_TOKEN)"
	@echo "  make clean                              - remove output/ and distilled/"
	@echo ""
	@echo "Vars: PLAYLIST=<url>  PLAYLIST_NAME=<dir>  MODE={talking-head,screen-heavy}  OUT=<dir>"
	@echo "      VIDEOS='10-25' (or '1,3,5-7')   JOBS=4 (parallel extract)"

scope:
	python3 scripts/scope_init.py --playlist $(PLAYLIST_NAME)

test1:
	python3 scripts/extract_playlist.py "$(PLAYLIST)" --playlist-name $(PLAYLIST_NAME) --mode $(MODE) --max-videos 1 --out $(OUT)

# Only forward a variable to the interactive front-end if the user
# actually set it (command line or environment) — Makefile defaults
# should NOT suppress prompts.
_user_set = $(if $(filter command\ line environment,$(origin $(1))),$($(1)),)

extract:
	@PLAYLIST="$(call _user_set,PLAYLIST)" \
	 PLAYLIST_NAME="$(call _user_set,PLAYLIST_NAME)" \
	 MODE="$(call _user_set,MODE)" \
	 VIDEOS="$(call _user_set,VIDEOS)" \
	 JOBS="$(call _user_set,JOBS)" \
	 OUT="$(OUT)" \
	 python3 scripts/extract_interactive.py

extract-batch:
	python3 scripts/extract_playlist.py "$(PLAYLIST)" --playlist-name $(PLAYLIST_NAME) $(EXTRACT_FLAGS)

preprocess:
	python3 scripts/preprocess_transcript.py --playlist $(PLAYLIST_NAME) --output-root $(OUT)

phase2:
	python3 scripts/run_phase2.py --playlist $(PLAYLIST_NAME) --output-root $(OUT)

phase3:
	python3 scripts/run_phase3.py --playlist $(PLAYLIST_NAME)

phase4:
	python3 scripts/run_phase4.py --playlist $(PLAYLIST_NAME) --mode $(SKILL_MODE)

topical:
	python3 scripts/run_topical.py --playlist $(PLAYLIST_NAME) --output-root $(OUT)

summary:
	python3 scripts/run_summary.py --playlist $(PLAYLIST_NAME) --output-root $(OUT)

stats:
	python3 scripts/run_stats.py --playlist $(PLAYLIST_NAME) --output-root $(OUT) --terms "$(THEMES)"

quote-mine:
	python3 scripts/quote_mine.py --playlist $(PLAYLIST_NAME) --output-root $(OUT) --themes "$(THEMES)"

screenshots:
	python3 scripts/capture_screenshots.py --playlist $(PLAYLIST_NAME) --output-root $(OUT)

citations:
	python3 scripts/citations.py --playlist $(PLAYLIST_NAME)

diff-synthesis:
	python3 scripts/diff_synthesis.py --old "$(OLD)" --new "$(NEW)" --out distilled/$(PLAYLIST_NAME)/CHANGELOG.md

eval:
	python3 scripts/run_eval.py --playlist $(PLAYLIST_NAME) --output-root $(OUT) --mode $(SKILL_MODE)

knowledge-diff:
	python3 scripts/knowledge_diff.py --playlist $(PLAYLIST_NAME) --output-root $(OUT)

test:
	python3 -m unittest discover -s tests -v

mcp:
	python3 scripts/mcp_server.py

mcp-http:
	python3 scripts/mcp_server.py --http $(if $(MCP_HOST),--allowed-host $(MCP_HOST))

clean:
	rm -rf $(OUT) distilled
