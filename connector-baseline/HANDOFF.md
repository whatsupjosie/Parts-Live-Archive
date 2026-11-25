# Installed: PubPartner connector baseline (Nov 21) (2026-11-25)

**Status: installed in main-assembly and proven. Copied to Parts-Live-Archive as `connector-baseline/`.**

## What it is

The local PubPartner connector: shared connection setup, ChatGPT consent and owner-managed MCP transports, a no-key Claude handoff and Claude desktop extension, finding and starting local chat models, safer account switching and consent recovery, cartridge validation, small models kept responsive on CPU, and connection readiness and local recovery repaired. Documented in docs/PUBPARTNER_CONNECTOR.md.

## Where it came from

pre-dec branch `codex/pubpartner-connector-baseline` (2963fd0, nine commits), installed through the studio line's merge ba93c311 (Nov 22). Nothing in it was changed on the way in. The original stays in its storage repo.

## What it changed in main-assembly

Installed on top of main-assembly 5396b0da: 24 files changed, 1957 insertions(+), 19 deletions(-). Every file it added or changed is in Parts-Live-Archive under `connector-baseline/files/`, and the exact change is `connector-baseline/INSTALL.patch`.

## How it was proven (Nov 25, in main-assembly after installing)

- Its own tests (the test files this piece added or changed: 2): 5 failed, 25 passed, 2 skipped, 2 warnings in 4.90s. Python 3.13 with fastapi 0.115.14, starlette 0.41.3, httpx 0.28.1. scikit-learn is not installed (not okayed), so a stand-in that fails loudly if anything actually calls it let the real host start. Browser tests that need playwright are skipped; async tests run without pytest-asyncio.
- Compile and syntax: py files 15  js 2  json 1; all clean.
- The app boots with it: main.py builds the app with 204 routes. Packages not installed here and not okayed to install (scikit-learn, numpy, bcrypt, psutil, websockets and some Windows-only ones) are empty stand-ins for this check only, so it proves the wiring, not those features.

## Notes

The connection tests borrow a helper from a browser test file that loads playwright at the top, so without playwright the whole file was skipped. To run them, a stand-in for playwright was added for this run only; any test that actually drives a browser fails at once. Result: 25 passed. The 5 failures are all things not installed here, not this piece: four drive a real browser (connection screen and saved key, cancelled callback, choosing a local model, late ChatGPT sign-in), and one (occupied connector port) restarts the local connector, which needs the optional MCP connector SDK. That same SDK is why tests/pubpartner_core/test_connector.py skipped, and one OpenAI tunnel test skipped for its optional client. This piece adds three lines to requirements.txt: PyJWT with crypto, mcp (the connector SDK) and sse-starlette. None of them were installed for this check; mcp is the one the skipped and failed connector tests need.
