# Cost Tuning

All settings referred to here are in `config.py`. Run
`python orchestrate.py "<brief>" --dry-run` to see the projected cost of each stage
before spending anything. During a real run, `run_record.py` meters spend from actual
token usage. If cumulative spend goes over `COST_CEILING_USD` (default `0.75`), the run
aborts.

## 1. Model choice per stage

The cheapest way to change cost is to change the model a stage uses. Prices per
1M tokens come from `GEMINI_PRICING`:

| Model | Input | Output | Thinking |
|---|---|---|---|
| `gemini-2.5-flash` | 0.15 | 0.60 | 3.50 |
| `gemini-3-flash-preview` | 0.50 | 3.00 | 3.00 |
| `gemini-2.5-pro` | 1.25 | 10.00 | 10.00 |
| `gemini-3.1-pro-preview` | 2.00 | 12.00 | 12.00 |

The current defaults are `gemini-3-flash-preview` for the Creative Director, the
Editorial Critic and Output-QA, and `gemini-2.5-flash` for the researcher. If a model
is missing from the table, it is costed at `GEMINI_PRICING_DEFAULT` and a log line
records this.

## 2. Thinking budgets

Thinking tokens are billed at or above the output rate, which makes them the biggest
per-call cost. `None` means uncapped and `0` means disabled.

| Setting | Default | Used by | Guidance |
|---|---|---|---|
| `THINKING_BUDGET_CREATIVE` | `None` | Creative Director (and its revisions) | When capped, hooks got weaker, clip descriptions repeated and narrative arcs became generic. Capping saves money at a cost to quality. |
| `THINKING_BUDGET_REVIEWER` | `None` | Editorial Critic and Output-QA | When capped, the critic became a rubber stamp and gave high scores to mediocre specs. |
| `THINKING_BUDGET_TIMESTAMPS` | `1024` | Researcher video analysis | The one place where a cap is safe. If timestamps are often wrong, raise it to `2048`. |
| `THINKING_BUDGET_CRITIQUE` | `None` | *(retired)* | Belonged to the v1 self-critique pass. It now only feeds the dry-run estimate. |

## 3. Researcher

| Setting | Default | Notes |
|---|---|---|
| `RESEARCHER_MAX_SEARCH_RESULTS` | `5` | yt-dlp search is free; this only affects ranking. |
| `RESEARCHER_MAX_DOWNLOAD_CANDIDATES` | `3` | Each extra candidate can mean one more Gemini video call. Lowering it causes more fallbacks. |
| `RESEARCHER_ALT_QUERIES` | `3` | Alternate queries tried before falling back to a still or a gradient. |

The researcher checks the video description and chapter markers for timestamps before
it uploads anything to Gemini. When metadata gives a match, the Gemini call is skipped.

## 4. Revision loop

`SPEC_REVIEW_MAX_RETRIES` (default `2`) caps how many times the Editorial Critic can
send a spec back to the Creative Director. In the worst case a run makes
`1 + SPEC_REVIEW_MAX_RETRIES` Creative Director calls, with one critic call after each.

## 5. Caches

None of the caches affect quality; they only stop identical work from being repeated.
Deleting a cache forces fresh API calls the next time that work is needed.

| Path | Contents |
|---|---|
| `cache/briefs/` | Brief → spec (bypass with `--force-regen`) |
| `cache/searches/` | YouTube search results by query |
| `cache/metadata/` | Video descriptions and chapters by video ID |
| `cache/timestamps/` | Gemini timestamp results by video + description hash |
| `cache/clip_knowledge/` | Cross-run clip knowledge base |
| `cache/stills/` | Stills for the Ken Burns fallback |
| `.clip_cache/` | Downloaded YouTube videos by video ID |
| `.trim_cache/` | Trimmed videos prepared for Gemini upload |

## 6. Rate limiting

`GEMINI_RPM_SLEEP` (default `2.5` seconds) is the delay between API calls and is set
for the free tier. On a paid tier it can go down to `0.5` or `0`. This changes speed
only, not cost.
