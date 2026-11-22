"""Main entry point for the MCP Claude POC application.

Starts the document MCP server (plus any --server scripts) as stdio
subprocesses, then runs the interactive chat (or a single --query).
"""

# --- Standard library imports
import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path

# --- Local application imports
from mcp_claude_poc import config
from mcp_claude_poc.client.mcp_client import MCPClient
from mcp_claude_poc.core.cli import CliApp
from mcp_claude_poc.agents.doc_agent import DocAgent
from mcp_claude_poc.core.claude_client import ClaudeClient

# Resolve paths from this file (not the current working directory).
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[1]
MCP_SERVER_SCRIPT = PACKAGE_DIR / "server" / "mcp_server.py"


def resolve_script_path(script_path: str | Path) -> Path:
    """Return the absolute path of an existing server script.

    Raises:
        FileNotFoundError: if the path does not point to an existing file.
    """
    path = Path(script_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Script not found: {path}")
    return path


def validate_env(args: list[str] | None = None) -> None:
    """Apply command-line overrides, then check required settings.

    The API key isn't returned: the Anthropic SDK reads ``ANTHROPIC_API_KEY``
    from the environment itself. It's checked here only to fail fast with a
    clear message instead of an SDK error on the first request.
    """
    config.apply_cli_args(args)

    if not config.ANTHROPIC_API_KEY:
        sys.exit("ANTHROPIC_API_KEY is missing. Add it to your .env file or pass --api-key.")
    if not config.MODEL:
        sys.exit("CLAUDE_MODEL is missing. Add it to your .env file.")


def get_launcher() -> tuple[str, list[str]]:
    """Return the executable and leading args used to run a server script.

    With USE_UV / --use-uv scripts run as ``uv run python <script>`` (from the
    project root, so uv picks up this project's environment); otherwise they
    run with the interpreter running this app, so they see the same packages.
    """
    if config.USE_UV:
        return "uv", ["run", "--project", str(PROJECT_ROOT), "python"]
    return sys.executable, []


async def main(args: list[str] | None = None) -> None:
    """Configure the app, start every MCP server, and run the chat."""
    validate_env(args)

    claude_client = ClaudeClient(config.MODEL, max_tokens=config.MAX_TOKENS, effort=config.EFFORT)

    # Validate every script before spawning any subprocess, so a typo fails
    # fast. Duplicates are dropped so the same server isn't launched twice.
    mcp_script = resolve_script_path(MCP_SERVER_SCRIPT)
    extra_scripts: list[Path] = []

    # Resolve paths for all extra server scripts and ensure they exist.
    for raw_path in config.EXTRA_SERVERS:
        path = resolve_script_path(raw_path)
        if path != mcp_script and path not in extra_scripts:
            extra_scripts.append(path)

    # Determine the command and base arguments for launching server scripts.
    command, base_args = get_launcher()

    # Launch the main MCP server and any extra servers within an async context.
    async with AsyncExitStack() as stack:
        doc_client = await stack.enter_async_context(
            MCPClient(command=command, args=[*base_args, str(mcp_script)], cwd=PROJECT_ROOT)
        )
        clients: dict[str, MCPClient] = {"doc_client": doc_client}

        for i, script in enumerate(extra_scripts):
            # Include the index so two scripts with the same name in different
            # folders still get unique IDs.
            clients[f"client_{i}_{script.stem}"] = await stack.enter_async_context(
                MCPClient(command=command, args=[*base_args, str(script)], cwd=PROJECT_ROOT)
            )

        agent = DocAgent(doc_client=doc_client, clients=clients, claude_client=claude_client)

        if config.QUERY:
            print(await agent.run(config.QUERY))
            return

        cli = CliApp(agent)
        await cli.initialize()
        await cli.run()


def run() -> None:
    """Console-script entry point (``mcp-claude-poc``)."""
    try:
        asyncio.run(main())
    except (FileNotFoundError, ConnectionError, OSError) as exc:
        # Configuration problem: show a short message, not a traceback.
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        # Ctrl+C: conventional exit code for SIGINT.
        print("Execution interrupted by user.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    run()
