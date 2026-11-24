"""The generated MCP servers expose exactly what mcp/exposure.json allows."""
from agent.config import generated_tool_names, systems


def test_generated_tools_match_the_allowlist():
    for spec in systems().values():
        got = set(generated_tool_names(spec.server_file()))
        base = {e["tool"] for e in spec.raw["expose"]}
        live = {e["tool"] for e in spec.raw.get("live_demo", [])}
        assert base <= got <= base | live, spec.key


def test_withheld_endpoints_never_reach_a_server():
    for spec in systems().values():
        code = spec.server_file().read_text()
        assert "/compensation" not in code
        assert "requests.delete" not in code
        assert "/payments/" not in code
        assert "/admin/reseed" not in code


def test_only_disable_and_revoke_write():
    writes = set()
    for spec in systems().values():
        writes |= spec.write_tools()
    assert writes == {"disable_account", "revoke_role"}
