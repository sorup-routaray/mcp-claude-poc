# Client for the Claude API via the Anthropic SDK (anthropic 2.x).
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types.beta import BetaMessage

# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
# On these models a safety-classifier decline is retried on a fallback model
# inside the same API call instead of just returning stop_reason="refusal".
FALLBACK_MODELS = {
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


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
            # MAX_TOKENS default in config.py; requests stream, so higher
            # values (up to the model's 128K output cap) work too.
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
    def text_from_message(self, message: BetaMessage) -> str:
        return "\n".join(block.text for block in message.content if block.type == "text")

    # Sends a request to the Claude API and returns the response message.
    async def send_message(
        self,
        # List of messages representing the conversation history.
        messages: list,
        # Optional system message to guide the model's behavior.
        system: str | None = None,
        # List of stop sequences to control response termination.
        stop_sequences: list[str] | None = None,
        # List of tools available for the model to use.
        tools: list[dict[str, Any]] | None = None,
    ) -> BetaMessage:
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

        betas: list[str] = []
        if self.model in FALLBACK_MODELS:
            betas.append(FALLBACK_BETA)
            params["fallbacks"] = "default"

        # Thinking is not configured explicitly: current models (Opus 5.5,
        # Sonnet 5.5, Fable 5.x) run adaptive thinking by default and reject
        # the old {"type": "enabled", "budget_tokens": N} form with a 400.
        #
        # Streaming instead of `messages.create()`: for a non-streaming call the
        # SDK estimates how long the response could take from `max_tokens`, and
        # above ~21,333 tokens it refuses the request up front ("Streaming is
        # required for operations that may take longer than 10 minutes").
        # Streaming has no such limit, so any --max-tokens value (up to the
        # model's 128K output cap) works. We don't show tokens as they arrive:
        # get_final_message() waits for the end of the stream and returns the
        # same BetaMessage that create() would have returned, so callers don't
        # change.
        async with self.client.beta.messages.stream(betas=betas, **params) as stream:
            return await stream.get_final_message()
