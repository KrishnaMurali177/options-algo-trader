"""Tests for the MCP Client (mocked — no real server needed)."""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.mcp_client import MCPClient


@pytest.fixture
def mcp_client():
    client = MCPClient(server_command="echo test")
    return client


class TestMCPClient:

    @pytest.mark.asyncio
    async def test_call_tool_raises_when_not_connected(self, mcp_client):
        with pytest.raises(RuntimeError, match="not connected"):
            await mcp_client.call_tool("get_account_info")

    @pytest.mark.asyncio
    async def test_list_tools_raises_when_not_connected(self, mcp_client):
        with pytest.raises(RuntimeError, match="not connected"):
            await mcp_client.list_tools()

    @pytest.mark.asyncio
    async def test_call_tool_returns_parsed_json(self, mcp_client):
        """Simulate a connected session returning JSON."""
        mock_content = MagicMock()
        mock_content.text = json.dumps({"buying_power": 50000})

        mock_result = MagicMock()
        mock_result.content = [mock_content]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        mcp_client._session = mock_session

        result = await mcp_client.call_tool("get_account_info")
        assert result == {"buying_power": 50000}
        mock_session.call_tool.assert_called_once_with("get_account_info", arguments={})

    @pytest.mark.asyncio
    async def test_call_tool_returns_text_on_non_json(self, mcp_client):
        """If server returns non-JSON text, return as string."""
        mock_content = MagicMock()
        mock_content.text = "Order submitted successfully"

        mock_result = MagicMock()
        mock_result.content = [mock_content]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        mcp_client._session = mock_session

        result = await mcp_client.call_tool("place_option_order", {"legs": []})
        assert result == "Order submitted successfully"

    @pytest.mark.asyncio
    async def test_convenience_wrappers(self, mcp_client):
        """Verify convenience methods pass correct tool name + args."""
        mock_content = MagicMock()
        mock_content.text = "[]"
        mock_result = MagicMock()
        mock_result.content = [mock_content]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        mcp_client._session = mock_session

        await mcp_client.get_positions("stock")
        mock_session.call_tool.assert_called_with("get_positions", arguments={"account_type": "stock"})

        await mcp_client.get_options_chain("AAPL", "2026-05-15", "call")
        mock_session.call_tool.assert_called_with(
            "get_options_chain",
            arguments={"symbol": "AAPL", "expiration": "2026-05-15", "option_type": "call"},
        )

