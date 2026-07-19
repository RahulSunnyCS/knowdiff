PLAYLIST ?= https://www.youtube.com/playlist?list=PLS2yrp7IIPNj-GKemcb5p8IrIA7EUwvTE
PLAYLIST_NAME ?= playlist
MODE ?= talking-head
OUT ?= output
SKILL_MODE ?= Teacher
# Optional: '10-25', '1,3,5-7', or a single number. Empty = all.
VIDEOS ?=
# Parallel video workers for the extract step. Safe with captions; keep
# at 1 for --force-whisper / screen-heavy on modest hardware.
JOBS ?= 1

# Network / rate-limit knobs. Empty by default; set on the command line when
# YouTube throttles you (429 / "confirm you're not a bot"). See README.
#   COOKIES_FROM=chrome   COOKIES=cookies.txt   PROXY=http://host:port
#   SLEEP=2 (between requests)   SLEEP_MIN / SLEEP_MAX (per download)
#   RETRIES=10   LIMIT_RATE=2M
NET_FLAGS =
ifneq ($(strip $(COOKIES_FROM)),)
NET_FLAGS += --cookies-from-browser $(COOKIES_FROM)
endif
ifneq ($(strip $(COOKIES)),)
NET_FLAGS += --cookies $(COOKIES)
endif
ifneq ($(strip $(PROXY)),)
NET_FLAGS += --proxy $(PROXY)
endif
ifneq ($(strip $(SLEEP)),)
NET_FLAGS += --sleep-requests $(SLEEP)
endif
ifneq ($(strip $(SLEEP_MIN)),)
NET_FLAGS += --sleep-interval $(SLEEP_MIN)
endif
ifneq ($(strip $(SLEEP_MAX)),)
NET_FLAGS += --max-sleep-interval $(SLEEP_MAX)
endif
ifneq ($(strip $(RETRIES)),)
NET_FLAGS += --retries $(RETRIES)
endif
ifneq ($(strip $(LIMIT_RATE)),)
NET_FLAGS += --limit-rate $(LIMIT_RATE)
endif

EXTRACT_FLAGS = --mode $(MODE) --out $(OUT) --jobs $(JOBS) $(NET_FLAGS)
ifneq ($(strip $(VIDEOS)),)
EXTRACT_FLAGS += --videos "$(VIDEOS)"
endif

.PHONY: help scope test1 extract extract-batch preprocess phase2 phase3 phase4 \
        topical summary stats quote-mine screenshots citations \
        diff-synthesis eval clean

help:
	@echo "Setup:"
	@echo "  make scope PLAYLIST_NAME=...           - interactive Phase 0 scoping"
	@echo ""
	@echo "Extract + prep (local, free):"
	@echo "  make test1                              - extract one video as sanity check"
	@echo "  make extract                            - interactive extract (prompts for url, range, jobs)"
	@echo "  make extract-batch PLAYLIST=... VIDEOS=10-25 JOBS=4   - non-interactive"
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
	@echo ""
	@echo "Audit + iterate:"
	@echo "  make citations PLAYLIST_NAME=...        - regenerate citations sidecar"
	@echo "  make diff-synthesis OLD=... NEW=...     - compare two synthesis.json"
	@echo "  make eval PLAYLIST_NAME=...             - hold-one-out scoring"
	@echo "  make clean                              - remove output/ and distilled/"
	@echo ""
	@echo "Vars: PLAYLIST=<url>  PLAYLIST_NAME=<dir>  MODE={talking-head,screen-heavy}  OUT=<dir>"
	@echo "      VIDEOS='10-25' (or '1,3,5-7')   JOBS=4 (parallel extract)"

scope:
	python3 scripts/scope_init.py --playlist $(PLAYLIST_NAME)

test1:
	python3 scripts/extract_playlist.py "$(PLAYLIST)" --mode $(MODE) --max-videos 1 --out $(OUT) $(NET_FLAGS)

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
	 COOKIES_FROM="$(COOKIES_FROM)" \
	 COOKIES="$(COOKIES)" \
	 PROXY="$(PROXY)" \
	 SLEEP="$(SLEEP)" \
	 SLEEP_MIN="$(SLEEP_MIN)" \
	 SLEEP_MAX="$(SLEEP_MAX)" \
	 RETRIES="$(RETRIES)" \
	 LIMIT_RATE="$(LIMIT_RATE)" \
	 python3 scripts/extract_interactive.py

extract-batch:
	python3 scripts/extract_playlist.py "$(PLAYLIST)" $(EXTRACT_FLAGS)

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
	python3 scripts/run_eval.py --playlist $(PLAYLIST_NAME) --output-root $(OUT)

clean:
	rm -rf $(OUT) distilled
