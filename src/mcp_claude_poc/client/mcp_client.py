import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client


class MCPClient:
    """Thin wrapper around an MCP ClientSession talking to a stdio server (mcp 2.x)."""

    def __init__(
        self,
        command: str,
        args: list[str],
        env: Optional[dict[str, str]] = None,
        cwd: str | Path | None = None,
    ):
        self._command = command
        self._args = args
        self._env = env
        self._cwd = cwd
        self._session: Optional[ClientSession] = None
        self._exit_stack: Optional[AsyncExitStack] = None

    # Connect to the MCP server using stdio transport and initialize the session
    async def connect(self) -> None:
        server_params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=self._env,
            cwd=self._cwd,
        )
        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(stdio_client(server_params))
        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    # Return the current client session, raising an error if not connected
    def session(self) -> ClientSession:
        if self._session is None:
            raise ConnectionError("Client session not initialized. Call connect() first.")
        return self._session

    # Close the client session and release resources (safe to call twice)
    async def close(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._session = None
        self._exit_stack = None

    async def __aenter__(self):
        try:
            await self.connect()
        except BaseException:
            # __aexit__ isn't called when __aenter__ raises, so close the
            # transport here (in this task) instead of leaving it for the GC.
            await self.close()
            raise
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    # List the tools available on the MCP server
    async def list_tools(self) -> list[types.Tool]:
        result = await self.session().list_tools()
        return result.tools

    # Call a particular tool on the MCP server
    async def call_tool(self, tool_name: str, tool_input: dict[str, Any]) -> types.CallToolResult:
        return await self.session().call_tool(tool_name, tool_input)

    # List the prompts available on the MCP server
    async def list_prompts(self) -> list[types.Prompt]:
        result = await self.session().list_prompts()
        return result.prompts

    # Get a specific prompt from the MCP server
    async def get_prompt(self, prompt_name: str, args: dict[str, str]) -> list[types.PromptMessage]:
        result = await self.session().get_prompt(prompt_name, args)
        return result.messages

    # Read a resource from the MCP server. JSON resources are decoded.
    async def read_resource(self, uri: str) -> Any:
        # mcp 2.x takes the URI as a plain str (1.x wanted pydantic AnyUrl).
        result = await self.session().read_resource(uri)
        if not result.contents:
            return None
        resource = result.contents[0]

        if isinstance(resource, types.TextResourceContents):
            # mcp 2.x models use snake_case fields (mime_type, not mimeType).
            if resource.mime_type == "application/json":
                return json.loads(resource.text)
            return resource.text
        return resource.blob  # base64 data for binary resources

    # List all resources from the MCP server, following pagination.
        # List all resources from the MCP server, following pagination.
    async def list_resources(self) -> list[types.Resource]:
        resources: list[types.Resource] = []
        cursor: str | None = None
        while True:
            params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
            result = await self.session().list_resources(params=params)
            resources.extend(result.resources)
            cursor = result.next_cursor
            if not cursor:
                return resources

    # List resource templates (URIs with placeholders like {doc_id}).
    async def list_resource_templates(self) -> list[types.ResourceTemplate]:
        templates: list[types.ResourceTemplate] = []
        cursor: str | None = None
        while True:
            params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
            result = await self.session().list_resource_templates(params=params)
            templates.extend(result.resource_templates)
            cursor = result.next_cursor
            if not cursor:
                return templates

    async def print_resources(self) -> None:
        for r in await self.list_resources():
            print(f"{r.uri}  |  {r.name}  |  {r.mime_type or '-'}")
            if r.description:
                print(f"    {r.description}")


# Manual smoke test: `uv run python -m mcp_claude_poc.client.mcp_client`
async def main() -> None:
    # Path to the MCP server script.
    server = Path(__file__).resolve().parents[1] / "server" / "mcp_server.py"
    # Launch the MCP client with the server script.
    async with MCPClient(command=sys.executable, args=[str(server)]) as client:
        tools, prompts, resources, templates = await asyncio.gather(
            client.list_tools(),
            client.list_prompts(),
            client.list_resources(),
            client.list_resource_templates(),
        )
        print("Tools:    ", [t.name for t in tools])
        print("Prompts:  ", [p.name for p in prompts])
        print("Resources:")
        for r in resources:
            print(f"  {r.uri}  |  {r.name}  |  {r.mime_type or '-'}")
        print("Resource Templates:")
        for t in templates:
            print(f"  {t.uri_template}  |  {t.name}  |  {t.mime_type or '-'}")


if __name__ == "__main__":
    # Run the manual smoke test for the MCP client.
    asyncio.run(main())
