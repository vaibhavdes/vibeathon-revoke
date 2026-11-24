# Notes on Synapse CLI 0.1.9

What we saw while building Revoke with `@2ndbrainlabs-ai/synapse-cli` 0.1.9 (Node 24, Python 3.13, macOS),
local Auto mode on two FastAPI services. Offered as feedback; each item says how Revoke deals with it.

## What worked well

- **Scanning and selection.** The tree-sitter scan found every FastAPI route in under a second, and
  `--endpoints "GET /a,POST /b"` exposes exactly that list. Our build script verifies the generated tool names
  against `mcp/exposure.json` on every build and has never needed to fail.
- **Docstrings become tool descriptions.** That made the legacy code's own docs the agent's instructions:
  we tuned the docstrings, not a separate prompt file.
- **Local Auto mode** generates a passthrough server without calling a model, so no code or data leaves the
  machine and no quota is used.
- **`--discover`** picked up the new activity endpoint in one rebuild; the server file path stayed the same, so
  no client config changed.

## Things we had to work around

| # | Observation | How we handled it |
|---|---|---|
| 1 | Query parameters are not part of the generated tool (a `GET /search?q=` route becomes a tool with no arguments). Verified with a spike app. | Every filter is a path parameter or a JSON body field. |
| 2 | Body fields the model leaves out are sent as explicit `null` (`{"note": null}`), so a FastAPI model with `note: str = ""` returns 422. Verified with a spike app. | Every body field is `Optional[...] = None` and validated in the handler. |
| 3 | Generated tools call `resp.raise_for_status()`, so on a 4xx the agent sees only "422 Client Error" and not the API's message. | Business errors return HTTP 200 with `{"ok": false, "error": "..."}` so the agent can correct itself. |
| 4 | All tool parameters are typed `Any`, so the JSON schema has no types. Some models then pass lists as strings. | The agent's MCP hub gives the model typed schemas (strings, a list for `emails`) and marks path parameters required. |
| 5 | `.synapse/surface_cache.json` is written with `"files": {"undefined": {}}`. In our runs the cache was never reused: a rebuild **without** `--discover` also found the new endpoint. | We always pass `--discover` after a code change, as the docs say, so behaviour matches the documented flow either way. |
| 6 | `--local --auto` resolves a model provider even though passthrough Auto calls no model. | `scripts/build_mcp.py` sets `SYNAPSE_LLM_PROVIDER=ollama`, which needs no key. |
| 7 | Rebuilds can write numbered files (`synapse_auto_server_2.py`). | `scripts/build_mcp.py` keeps one stable `synapse_auto_server.py` either way, so the client config never changes. |

## A suggestion

Auto mode's passthrough servers are ideal for read tools. For write tools, an optional flag that generates a
two-step tool (preview, then confirm with a token) would let any MCP client get a safe confirmation flow without
an agent-side gate like Revoke's.
