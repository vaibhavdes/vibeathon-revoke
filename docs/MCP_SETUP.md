# MCP setup

Synapse turns each legacy system into its own MCP server. Nothing in either system's code was written for
MCP; Synapse reads the FastAPI routes and docstrings and generates the server.

## Build

```bash
python -m scripts.build_mcp
```

This runs, in each system's folder, exactly:

```bash
# systems/peoplehub
synapse build --local --auto --yes --server-name peoplehub \
  --endpoints "POST /employees/lookup,GET /employees/leavers/since/{since_date},GET /employees/job-changes/since/{since_date},GET /employees/{employee_id},GET /managers/{employee_id}/reports"

# systems/ledgerly
synapse build --local --auto --yes --server-name ledgerly \
  --endpoints "GET /users/active,GET /users/{username},GET /roles,GET /roles/{role}/members,GET /sod/rules,POST /users/{username}/disable,POST /users/{username}/roles/revoke"
```

The endpoint lists come from [`mcp/exposure.json`](../mcp/exposure.json), which also records why each endpoint is
exposed or withheld. The script then checks that the generated tools are exactly that list.

- `--local`: generated on this machine. Auto mode without `--smart-names` calls no model, so no code or
  data leaves the machine and no Synapse quota is used. (Synapse 0.1.9 still asks for a provider; the
  script sets `SYNAPSE_LLM_PROVIDER=ollama`, which needs no key.)
- Output: `systems/<system>/synapse_auto_server.py`, committed exactly as Synapse wrote it.

## Use the servers from Claude Desktop, Cursor or any MCP client

Start the systems (`python -m scripts.run_systems`), then add both servers to your client. For Claude Desktop:
Settings → Developer → Edit Config, and paste [`mcp/claude_desktop_config.example.json`](../mcp/claude_desktop_config.example.json)
with your absolute paths. Each server needs only `ENDPOINT_BASE_URL`; there are no secrets.

In a generic client the write tools (`disable_account`, `revoke_role`) are guarded only by the client's own
tool-approval prompt. The Revoke agent adds stronger rules: it never gives the model the write tools at all,
checks every proposed change against policy, and runs it only after a named person approves it.

## Live update

```bash
python -m scripts.live_update apply      # adds GET /users/{username}/activity to Ledgerly
python -m scripts.build_mcp --system ledgerly --discover --include-live
```

`--discover` rescans the code and the rebuilt server gains `get_account_activity` (12 → 13 tools). The
agent opens a fresh MCP session for every question, so the next question uses the new tool without a
restart or a config change. `python -m scripts.reset_demo` undoes it.
