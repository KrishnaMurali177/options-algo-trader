"""MCP Client — connects to the Robinhood MCP server over stdio transport."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config.settings import settings

logger = logging.getLogger(__name__)


class MCPClient:
    """Wrapper around the MCP SDK client for communicating with robinhood-mcp."""

    def __init__(self, server_command: str | None = None):
        self.server_command = server_command or settings.mcp_server_command
        self._session: ClientSession | None = None
        self._cm = None  # context manager reference

    # ── Lifecycle ─────────────────────────────────────────────────

    async def connect(self) -> None:
        """Start the MCP server process and establish a session."""
        parts = self.server_command.split()
        server_params = StdioServerParameters(command=parts[0], args=parts[1:])

        self._read, self._write = await asyncio.get_event_loop().run_in_executor(
            None, lambda: (None, None)
        )
        # Use the stdio_client context manager
        self._cm = stdio_client(server_params)
        streams = await self._cm.__aenter__()
        self._session = ClientSession(*streams)
        await self._session.__aenter__()
        await self._session.initialize()
        logger.info("MCP session established with server: %s", self.server_command)

    async def disconnect(self) -> None:
        """Tear down MCP session and server process."""
        if self._session:
            await self._session.__aexit__(None, None, None)
        if self._cm:
            await self._cm.__aexit__(None, None, None)
        logger.info("MCP session closed.")

    # ── Tool Invocation ───────────────────────────────────────────

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke an MCP tool by name and return the parsed result."""
        if not self._session:
            raise RuntimeError("MCP session not connected. Call connect() first.")

        logger.info("MCP call_tool: %s(%s)", name, arguments or {})
        result = await self._session.call_tool(name, arguments=arguments or {})

        # MCP results come back as a list of content blocks
        if result.content:
            text = result.content[0].text
            try:
                return json.loads(text)
            except (json.JSONDecodeError, AttributeError):
                return text
        return None

    async def list_tools(self) -> list[dict]:
        """List all tools exposed by the MCP server."""
        if not self._session:
            raise RuntimeError("MCP session not connected.")
        tools = await self._session.list_tools()
        return [{"name": t.name, "description": t.description} for t in tools.tools]

    # ── Convenience Wrappers ──────────────────────────────────────

    async def get_account_info(self) -> dict:
        return await self.call_tool("get_account_info")

    async def get_positions(self, account_type: str = "options") -> list[dict]:
        return await self.call_tool("get_positions", {"account_type": account_type})

    async def get_options_chain(
        self, symbol: str, expiration: str, option_type: str = "call"
    ) -> list[dict]:
        return await self.call_tool(
            "get_options_chain",
            {"symbol": symbol, "expiration": expiration, "option_type": option_type},
        )

    async def place_option_order(self, order_payload: dict) -> dict:
        return await self.call_tool("place_option_order", order_payload)

    async def cancel_order(self, order_id: str) -> dict:
        return await self.call_tool("cancel_order", {"order_id": order_id})

    async def get_market_data(
        self, symbol: str, interval: str = "day", span: str = "year"
    ) -> list[dict]:
        return await self.call_tool(
            "get_market_data",
            {"symbol": symbol, "interval": interval, "span": span},
        )

