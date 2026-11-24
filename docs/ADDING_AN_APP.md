# Adding an app

Revoke reviews one **app** (`"role": "app"`) against one **HR** system of record (`"role": "hr"`), both reached only
through MCP servers that Synapse generates. In the demo these are Ledgerly and PeopleHub. To review a different
in-house app, nothing in `agent/` changes:

1. **Generate its MCP server.** Add the app under `systems/<name>` (or point `path` at its code) and run
   `python -m scripts.build_mcp --system <name>`. Synapse reads the routes; the app itself is not modified.
2. **Say which endpoints become tools** in [`mcp/exposure.json`](../mcp/exposure.json): `expose` (with `access`
   `read` or `write`) and `withheld`, each with a reason. Write tools only ever run after a person approves them.
3. **Map its names to Revoke's**, on the same entries, only where they differ:

```json
{"endpoint": "GET /api/users", "tool": "get_users_list", "access": "read",
 "as": "list_active_accounts",
 "fields": {"accounts": "users", "username": "login", "email": "mail", "roles": "groups", "last_login": "lastSeen"},
 "values": {"roles": {"approver": "PAYMENT_APPROVER"}}}
```

- `as`: Revoke's standard tool name, when Synapse named the tool after the app's own function.
- `fields`: `{revoke field: app field}`. Replies are renamed on the way in, arguments on the way out.
- `values`: `{revoke field: {app value: revoke value}}`, for status codes or role names.

The MCP hub ([`agent/hub.py`](../agent/hub.py)) applies the mapping to every call, so the join, the policy gate,
the grounding checks and the evidence pack work unchanged. [`tests/test_field_mapping.py`](../tests/test_field_mapping.py)
shows an app with different field names going through it.

**Standard tools for the app:** `list_active_accounts`, `get_account`, `get_role_catalog`, `list_sod_rules`, and
optionally `list_role_members`, `get_account_activity`, `disable_account`, `revoke_role`.
**For HR:** `lookup_employees_by_email`, `list_leavers_since`, `list_job_changes_since`, `get_employee`,
`list_direct_reports`.

**What is still per company:** the policy constants in [`agent/grounding.py`](../agent/grounding.py) (which roles move
money, the dormant threshold) and the role catalog's `typical_titles`, which come from the app itself. Several apps
are reviewed one at a time, each with its own exposure file.
