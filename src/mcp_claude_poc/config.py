"""Application settings: read from .env / environment, overridable from the command line."""
import argparse
import os

from dotenv import load_dotenv

# Load variables from the project's .env file (searched upward from this file).
load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("true", "1", "t", "yes")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
# Thinking is always on for current models and counts toward max_tokens,
# so a small value (e.g. 1024) truncates answers. 16000 is a safe default.
MAX_TOKENS = _env_int("MAX_TOKENS", 16000)
# Optional: low | medium | high | xhigh | max (Opus 5.5 defaults to medium).
EFFORT = os.getenv("CLAUDE_EFFORT") or None
# Launch MCP servers with `uv run` instead of the current Python interpreter.
USE_UV = _env_bool("USE_UV")

# One-shot question from --query (None = interactive chat)
QUERY: str | None = None
# Extra MCP server scripts from --server
EXTRA_SERVERS: list[str] = []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-claude-poc",
        description="Chat with Claude using tools from an MCP server.",
    )
    parser.add_argument("--api-key",
                        help="Anthropic API key (overrides ANTHROPIC_API_KEY; "
                             "note: visible in shell history and process list)")
    parser.add_argument("--model", help=f"Claude model ID (default: {MODEL})")
    parser.add_argument("--max-tokens", type=int,
                        help=f"Max tokens per response (default: {MAX_TOKENS})")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                        help="Effort level (default: model default)")
    parser.add_argument("--use-uv", action=argparse.BooleanOptionalAction,
                        help=f"Launch MCP servers with 'uv run' (default: {USE_UV})")
    parser.add_argument("-q", "--query",
                        help="Ask one question, print the answer, then exit")
    parser.add_argument("--server", dest="extra_servers", action="append", default=[],
                        metavar="SCRIPT",
                        help="Path to an extra MCP server script (repeatable)")
    return parser


def apply_cli_args(argv: list[str] | None = None) -> None:
    """Parse command-line arguments (sys.argv when argv is None) and override settings."""
    global ANTHROPIC_API_KEY, MODEL, MAX_TOKENS, EFFORT, USE_UV, QUERY, EXTRA_SERVERS

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.api_key is not None:
        if not args.api_key.strip():
            parser.error("--api-key must not be empty")
        ANTHROPIC_API_KEY = args.api_key.strip()
        # The Anthropic SDK reads the key from the environment.
        os.environ["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
    if args.model is not None:
        MODEL = args.model
    if args.max_tokens is not None:
        if args.max_tokens <= 0:
            parser.error("--max-tokens must be a positive number")
        MAX_TOKENS = args.max_tokens
    if args.effort is not None:
        EFFORT = args.effort
    if args.use_uv is not None:
        USE_UV = args.use_uv
    QUERY = args.query
    EXTRA_SERVERS = args.extra_servers
