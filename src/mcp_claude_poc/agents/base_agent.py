import json
from typing import Any

import anyio
from mcp import MCPError, types

from mcp_claude_poc.client.mcp_client import MCPClient
from mcp_claude_poc.core.claude_client import ClaudeClient

# Upper bound on Claude <-> tool round trips for a single user query, so a
# misbehaving tool loop can't run (and bill) forever.
MAX_TOOL_ROUNDS = 20

# Errors that mean the MCP server's stdio connection is gone (subprocess
# crashed or exited). These are not tool errors: retrying can't help, so they
# are raised to the CLI instead of being handed back to Claude.
CONNECTION_ERRORS = (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream)


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
                except MCPError as exc:
                    # The server ran the request and reported an error (bad
                    # arguments, unknown document, timeout, ...). Claude can
                    # read this and recover, e.g. by retrying with other input.
                    content, is_error = f"Tool error: {exc}", True
                except CONNECTION_ERRORS as exc:
                    # The server is gone. Fail the turn with a clear message
                    # instead of letting Claude retry against a dead server
                    # for up to MAX_TOOL_ROUNDS paid requests.
                    raise ConnectionError(
                        f"MCP server for tool '{block.name}' is no longer reachable"
                    ) from exc
                else:
                    # Outside the try on purpose: a bug in our own formatting
                    # code should surface as a real error, not as a "tool error"
                    # sent to Claude.
                    content, is_error = self._tool_result_text(result), result.is_error
                # Any other exception (a bug in this code or in the client)
                # propagates as well; run() keeps the history valid before
                # re-raising it.
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                }
            )
        return results

    # Close out tool calls that were never run, so the history stays valid.
    #
    # The API requires every tool_use block in an assistant turn to be answered
    # by a tool_result in the next user turn; otherwise every later request in
    # this session fails with a 400. Rather than deleting the assistant turn
    # (which would erase text the user already saw and edit history, which
    # breaks thinking blocks on current models), we *append* one error
    # tool_result per tool_use. History stays append-only, and Claude can see
    # on the next turn that those calls did not happen.
    def _close_unrun_tools(self, response, reason: str) -> None:
        results = [
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": f"Not run: {reason}",
                "is_error": True,
            }
            for block in response.content
            if block.type == "tool_use"
        ]
        if results:
            self.messages.append({"role": "user", "content": results})

    async def run(self, query: str) -> str:
        await self._process_query(query)
        tools, routes = await self._collect_tools()

        # Iterate through tool-call rounds, sending the conversation history
        # to Claude and handling tool_use blocks until either a final answer
        # is produced or the maximum number of rounds is reached.
        for _ in range(MAX_TOOL_ROUNDS):
            response = await self.claude_client.send_message(self.messages, tools=tools)
            self.claude_client.add_message(self.messages, response, "assistant")

            if response.stop_reason == "tool_use":
                try:
                    tool_results = await self._run_tools(response, routes)
                except BaseException:
                    # A tool call failed hard (server gone, bug, Ctrl+C). Answer
                    # the pending tool_use blocks before re-raising so the next
                    # question in this session doesn't hit a 400.
                    self._close_unrun_tools(response, "the tool call failed before completing.")
                    raise
                self.messages.append({"role": "user", "content": tool_results})
                continue

            text = self.claude_client.text_from_message(response)
            if response.stop_reason == "refusal":
                category = response.stop_details.category if response.stop_details else None
                return f"[Claude declined this request (category: {category})]"
            if response.stop_reason == "max_tokens":
                # The reply was cut off. If it was cut off while writing a tool
                # call, that call is incomplete and is not run; close it out so
                # the turn (and the text the user sees) stays in history.
                self._close_unrun_tools(
                    response, "the response hit max_tokens before this tool call was complete."
                )
                text += "\n[Response truncated: raise MAX_TOKENS / --max-tokens]"
            return text

        return "[Stopped: too many tool-call rounds for one question]"
