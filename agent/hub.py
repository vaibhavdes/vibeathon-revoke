"""Connects to the MCP servers Synapse generated, one stdio process per system.

Every tool is exposed under "<namespace>__<tool>" (hr__..., ledgerly__...), so
the model always knows which system a fact came from. A hub is opened per
question, so a server rebuilt by `synapse build --discover` is picked up on
the next question without restarting anything.
"""
from __future__ import annotations

import json
import os
import re
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.config import ROOT, SystemSpec, systems

LOG_DIR = ROOT / "runtime"


def rename_keys(data: Any, mapping: dict[str, str]) -> Any:
    """Rename dict keys anywhere in a tool result or argument set (mapping: from -> to)."""
    if not mapping:
        return data
    if isinstance(data, dict):
        return {mapping.get(k, k): rename_keys(v, mapping) for k, v in data.items()}
    if isinstance(data, list):
        return [rename_keys(x, mapping) for x in data]
    return data


def map_values(data: Any, values: dict[str, dict]) -> Any:
    """Translate an app's own codes to Revoke's (e.g. status "left" -> "terminated"), after renaming."""
    if not values:
        return data
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            table = values.get(k)
            if table and isinstance(v, str):
                out[k] = table.get(v, v)
            elif table and isinstance(v, list):
                out[k] = [table.get(x, x) if isinstance(x, str) else x for x in v]
            else:
                out[k] = map_values(v, values)
        return out
    if isinstance(data, list):
        return [map_values(x, values) for x in data]
    return data


@dataclass
class HubTool:
    qualified: str
    system: str
    name: str
    description: str
    schema: dict
    access: str            # "read" or "write"
    endpoint: str = ""
    # From mcp/exposure.json: {"revoke field": "app field"} and {"revoke field": {"app value": "revoke value"}}.
    # Empty for the two demo apps, whose fields already use Revoke's names.
    fields: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)

    def to_revoke(self, data: Any) -> Any:
        return map_values(rename_keys(data, {v: k for k, v in self.fields.items()}), self.values)

    def to_app(self, arguments: dict) -> dict:
        return rename_keys(arguments, self.fields)

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.qualified, "description": f"[{self.system}] {self.description}",
            "parameters": self.schema}}


def _clean_schema(schema: dict, required: list[str]) -> dict:
    """Synapse types every parameter as Any. Give the model plain string fields
    (lists for the email lookup) and mark path parameters required."""
    props = {}
    for name, prop in (schema.get("properties") or {}).items():
        if name == "emails":
            props[name] = {"type": "array", "items": {"type": "string"}, "description": "email addresses"}
        else:
            props[name] = {"type": "string", "description": prop.get("title", name)}
    out = {"type": "object", "properties": props}
    req = [r for r in required if r in props]
    if req:
        out["required"] = req
    return out


@dataclass
class McpHub:
    specs: list[SystemSpec] = field(default_factory=lambda: list(systems().values()))

    def __post_init__(self):
        self._stack: AsyncExitStack | None = None
        self.sessions: dict[str, ClientSession] = {}
        self.tools: dict[str, HubTool] = {}

    async def __aenter__(self) -> "McpHub":
        self._stack = AsyncExitStack()
        LOG_DIR.mkdir(exist_ok=True)
        errlog = self._stack.enter_context(open(LOG_DIR / "mcp_servers.log", "a"))
        try:
            for spec in self.specs:
                await self._connect(spec, errlog)
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def _connect(self, spec: SystemSpec, errlog) -> None:
        server_file = spec.server_file()
        if server_file is None:
            raise RuntimeError(f"No generated MCP server for {spec.key}. Run: python -m scripts.build_mcp")
        params = StdioServerParameters(
            command=sys.executable, args=[str(server_file)], cwd=str(spec.path),
            env={**os.environ, "ENDPOINT_BASE_URL": spec.base_url})
        read, write = await self._stack.enter_async_context(stdio_client(params, errlog=errlog))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self.sessions[spec.key] = session
        meta = {e["tool"]: e for e in spec.endpoints(include_live=True)}
        listed = await session.list_tools()
        for tool in listed.tools:
            info = meta.get(tool.name, {})
            fields, values = info.get("fields", {}), info.get("values", {})
            back = {v: k for k, v in fields.items()}
            endpoint = info.get("endpoint", "")
            # "as" gives the tool Revoke's standard name when Synapse named it after the app's own code.
            name = info.get("as", tool.name)
            required = [back.get(p, p) for p in re.findall(r"{(\w+)}", endpoint)]
            if name == "lookup_employees_by_email":
                required.append("emails")
            schema = dict(tool.inputSchema or {})
            schema["properties"] = {back.get(k, k): v for k, v in (schema.get("properties") or {}).items()}
            qualified = f"{spec.namespace}__{name}"
            self.tools[qualified] = HubTool(
                qualified=qualified, system=spec.key, name=tool.name,
                description=tool.description or "", schema=_clean_schema(schema, required),
                access=info.get("access", "write"),   # unknown tools are treated as writes
                endpoint=endpoint, fields=fields, values=values)

    async def __aexit__(self, *exc) -> None:
        if self._stack:
            await self._stack.aclose()

    def is_write(self, qualified: str) -> bool:
        tool = self.tools.get(qualified)
        return tool is None or tool.access != "read"

    async def call(self, qualified: str, arguments: dict | None = None) -> tuple[Any, bool]:
        """Call a tool. Returns (parsed result, is_error)."""
        tool = self.tools.get(qualified)
        if tool is None:
            return {"error": f"Unknown tool {qualified}."}, True
        result = await self.sessions[tool.system].call_tool(tool.name, tool.to_app(arguments or {}))
        text = "".join(getattr(c, "text", "") for c in result.content)
        try:
            data = json.loads(text)
        except ValueError:
            data = {"text": text}
        is_error = bool(result.isError) or (isinstance(data, dict) and data.get("ok") is False)
        return tool.to_revoke(data), is_error
