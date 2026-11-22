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
            # Maximum number of tokens to generate in the response.
            max_tokens: int = 1024, 
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

    # Appends an assistant/user turn. For a model response the full content
    # list is kept (text, tool_use, thinking blocks, ...): thinking blocks must
    # be sent back unchanged on later requests.
    def add_message(self, messages: list, message: BetaMessage | str, role: str) -> None:
        messages.append(
            {
                "role": role,
                "content": message.content if isinstance(message, BetaMessage) else str(message),
            }
        )

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
        return await self.client.beta.messages.create(betas=betas, **params)
