# Client for the Claude API via the Anthropic SDK (anthropic 2.x).
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message

from mcp_claude_poc.core.call_logger import logged_call

claude_models = [
    {"name": "Haiku 5.5",  "api_id": "claude-haiku-5-5",  "input": 0.10, "output": 0.50},   # prompts <= 100K tokens
    {"name": "Sonnet 5.5", "api_id": "claude-sonnet-5-5", "input": 2.00, "output": 10.00},
    {"name": "Opus 5.5",   "api_id": "claude-opus-5-5",   "input": 4.00, "output": 20.00},
    {"name": "Fable 5.2",  "api_id": "claude-fable-5-2",  "input": 8.00, "output": 40.00},
]
# Simple list of names only
model_names = [m["name"] for m in claude_models]
# ['Haiku 5.5', 'Sonnet 5.5', 'Opus 5.5', 'Fable 5.2']

# Simple list of API model strings only
model_ids = [m["api_id"] for m in claude_models]
# ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-2']


class ClaudeClient:
    """
    Client for the Claude API via the Anthropic SDK.
    Handles message construction, sending, and response parsing.
    """
    def __init__(
            self,           
            # Model to use for the Claude API requests.
            model: str, 
            # Maximum number of tokens to generate in the response. Thinking
            # is always on for current models and counts toward this limit,
            # so a small value (e.g. 1024) cuts answers off. 16000 matches the
            # MAX_TOKENS default in config.py. Values above ~21K need
            # stream_message(); send_message() is refused by the SDK there.
            max_tokens: int = 16000,
            # Effort level for the model's output generation.
            effort: str | None = None
        ):
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        # Async client so API calls don't block the asyncio event loop that
        # also drives the MCP stdio sessions and the prompt_toolkit UI.
        # Credentials are resolved from the environment (ANTHROPIC_API_KEY, ...).
        self.client = AsyncAnthropic()

    # Extracts the text content from a message object.
    def text_from_message(self, message: Message) -> str:
        return "\n".join(block.text for block in message.content if block.type == "text")

    # Builds the request params shared by send_message() and stream_message().
    def _build_params(
        self,
        # List of messages representing the conversation history.
        messages: list,
        # Optional system message to guide the model's behavior.
        system: str | None = None,
        # List of stop sequences to control response termination.
        stop_sequences: list[str] | None = None,
        # List of tools available for the model to use.
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        # Optional params are only sent when set: the SDK uses `omit` for
        # "not given", and an explicit None would be sent as JSON null.
        if stop_sequences:
            params["stop_sequences"] = stop_sequences
        if tools:
            params["tools"] = tools
        if system:
            params["system"] = system
        if self.effort:
            params["output_config"] = {"effort": self.effort}

        # Thinking is not configured explicitly: current models (Opus 5.5,
        # Sonnet 5.5, Fable 5.x) run adaptive thinking by default and reject
        # the old {"type": "enabled", "budget_tokens": N} form with a 400.
        return params

    # Regular (non-streaming) request: returns the complete response message.
    # The SDK refuses non-streaming calls whose max_tokens implies they could
    # take over 10 minutes (above ~21,333 tokens) - use stream_message() then.
    async def send_message(
        self,
        messages: list,
        system: str | None = None,
        stop_sequences: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        params = self._build_params(messages, system, stop_sequences, tools)
        async with logged_call("claude", "messages.create", self.model, params) as rec:
            rec.response = await self.client.messages.create(**params)
        return rec.response

    # Streaming request: no max_tokens timeout limit, so any value up to the
    # model's 128K output cap works. get_final_message() waits for the end of
    # the stream and returns the same Message that send_message() would.
    async def stream_message(
        self,
        messages: list,
        system: str | None = None,
        stop_sequences: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        params = self._build_params(messages, system, stop_sequences, tools)
        async with logged_call("claude", "messages.stream", self.model, params) as rec:
            async with self.client.messages.stream(**params) as stream:
                rec.response = await stream.get_final_message()
        return rec.response
