PLAYLIST ?= https://www.youtube.com/playlist?list=PLS2yrp7IIPNj-GKemcb5p8IrIA7EUwvTE
PLAYLIST_NAME ?= playlist
MODE ?= talking-head
OUT ?= output
SKILL_MODE ?= Teacher

.PHONY: help scope test1 extract preprocess phase2 phase3 phase4 \
        topical summary stats quote-mine screenshots citations \
        diff-synthesis eval knowledge-diff claim-cards test mcp mcp-http clean

help:
	@echo "Setup:"
	@echo "  make scope PLAYLIST_NAME=...           - interactive Phase 0 scoping"
	@echo ""
	@echo "Extract + prep (local, free):"
	@echo "  make test1                              - extract one video as sanity check"
	@echo "  make extract                            - extract full playlist"
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
	@echo "  make claim-cards                        - rebuild the clustered claim-card index (\$$0)"
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

scope:
	python3 scripts/scope_init.py --playlist $(PLAYLIST_NAME)

test1:
	python3 scripts/extract_playlist.py "$(PLAYLIST)" --playlist-name $(PLAYLIST_NAME) --mode $(MODE) --max-videos 1 --out $(OUT)

extract:
	python3 scripts/extract_playlist.py "$(PLAYLIST)" --playlist-name $(PLAYLIST_NAME) --mode $(MODE) --out $(OUT)

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

claim-cards:
	python3 scripts/claim_cards.py

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
