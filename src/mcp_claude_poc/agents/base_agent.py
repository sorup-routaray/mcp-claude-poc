import json
from typing import Any

from mcp import MCPError, types

from mcp_claude_poc.client.mcp_client import MCPClient
from mcp_claude_poc.core.claude_client import ClaudeClient

# Upper bound on Claude <-> tool round trips for a single user query, so a
# misbehaving tool loop can't run (and bill) forever.
MAX_TOOL_ROUNDS = 20


class Agent:
    """Tool-use agent loop: Claude picks MCP tools, the agent runs them and
    feeds the results back until Claude has a final answer."""
    def __init__(
        self,
        claude_client: ClaudeClient,
        clients: dict[str, MCPClient],
    ):
        self.claude_client: ClaudeClient = claude_client
        self.clients: dict[str, MCPClient] = clients
        # Holds SDK param dicts and, for assistant turns, SDK content blocks.
        self.messages: list[dict[str, Any]] = []

    # Subclasses turn the raw query into one or more user messages.
    async def _process_query(self, query: str) -> None:
        self.messages.append({"role": "user", "content": query})

    # Collect tools from every MCP server, in Anthropic tool format, plus a
    # name -> client map used to route tool calls back to the right server.
    async def _collect_tools(self) -> tuple[list[dict[str, Any]], dict[str, MCPClient]]:
        tools: list[dict[str, Any]] = []
        routes: dict[str, MCPClient] = {}
        for client in self.clients.values():
            for tool in await client.list_tools():
                if tool.name in routes:
                    continue  # first server wins on a name clash
                routes[tool.name] = client
                tools.append(
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.input_schema,
                    }
                )
        return tools, routes

    @staticmethod
    def _tool_result_text(result: types.CallToolResult) -> str:
        parts: list[str] = []
        for block in result.content:
            if isinstance(block, types.TextContent):
                parts.append(block.text)
            else:
                parts.append(json.dumps(block.model_dump(mode="json")))
        if not parts and result.structured_content is not None:
            parts.append(json.dumps(result.structured_content))
        return "\n".join(parts)

    async def _run_tools(self, response, routes: dict[str, MCPClient]) -> list[dict[str, Any]]:
        # All tool_result blocks for one assistant turn go back in ONE user message.
        results: list[dict[str, Any]] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            client = routes.get(block.name)
            if client is None:
                content, is_error = f"Unknown tool: {block.name}", True
            else:
                try:
                    result = await client.call_tool(block.name, dict(block.input or {}))
                    content, is_error = self._tool_result_text(result), result.is_error
                except MCPError as exc:
                    content, is_error = f"Tool error: {exc}", True
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                }
            )
        return results

    async def run(self, query: str) -> str:
        await self._process_query(query)
        tools, routes = await self._collect_tools()

        for _ in range(MAX_TOOL_ROUNDS):
            response = await self.claude_client.send_message(self.messages, tools=tools)
            self.claude_client.add_message(self.messages, response, "assistant")

            if response.stop_reason == "tool_use":
                self.messages.append(
                    {"role": "user", "content": await self._run_tools(response, routes)}
                )
                continue

            text = self.claude_client.text_from_message(response)
            if response.stop_reason == "refusal":
                category = response.stop_details.category if response.stop_details else None
                return f"[Claude declined this request (category: {category})]"
            if response.stop_reason == "max_tokens":
                text += "\n[Response truncated: raise MAX_TOKENS / --max-tokens]"
            return text

        return "[Stopped: too many tool-call rounds for one question]"
