"""Message roles used in the Claude conversation history.

The Messages API only accepts these exact strings as the `role` of a turn,
so they are defined once here instead of being typed as literals around the
code, where a typo ("asistant") would only fail at request time.
"""
from typing import Final, Literal

# Type for a role value: lets type checkers catch an invalid role.
Role = Literal["user", "assistant"]

# A turn written by the user side: questions, prompts and tool_result blocks.
ROLE_USER: Final[Role] = "user"

# A turn written by Claude: its response content (text, tool_use, thinking).
ROLE_ASSISTANT: Final[Role] = "assistant"
