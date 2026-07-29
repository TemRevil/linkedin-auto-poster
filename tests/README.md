# Tests

Fast, dependency-light unit tests for the pure/importable modules under `src/`.
They use the standard library `unittest` (no `pytest` required) and never touch
the real `discovery.db`, `sessions.json`, or settings — anything stateful is
pointed at a temp directory inside the test.

## Run

From the project root:

```bash
python -m unittest discover -s tests -v
```

`tests/_bootstrap.py` puts `src/` on `sys.path`, so importing `discover -s tests`
is the supported entry point (it adds the `tests/` dir to the path so
`import _bootstrap` resolves in each module).

## What's covered

| File | Module under test |
|------|-------------------|
| `test_humanizer.py` | `humanizer` — AI-pattern detection, em-dash/emoji/filler cleanup |
| `test_hashtags.py` | `post_generator.generate_hashtags` — AI + custom topics |
| `test_image_query.py` | `post_generator.suggest_image_query` / `get_image_urls` |
| `test_swipe_and_prefs.py` | `database` — swipe idempotency, manual keyword prefs |
| `test_keywords_striphtml.py` | `news_discovery` — `strip_html`, `extract_keywords`, clustering |
| `test_clean_connections.py` | `clean_connections` — session resolver guards, `clean_name` |
| `test_config.py` | `config` — `get_setting`, topic → query derivation |
| `test_gmail_pref.py` | `news_discovery._extract_gmail_preferences` fallback |
| `test_helpers.py` | shared helpers `_title_tokens`, `_reduce_em_dashes`, `_delta_days` |

Modules that require network/browser deps at import time (`approval_server`
needs Flask, `news_scraper` needs feedparser/Playwright) are intentionally not
imported here; their logic is validated separately.
