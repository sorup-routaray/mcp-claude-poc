"""Project-wide constants, grouped by topic in submodules.

Import from the package, not the submodule, so files can be reorganised
without touching callers:

    from mcp_claude_poc.constants import ROLE_USER, ROLE_ASSISTANT
"""
from mcp_claude_poc.constants.roles import ROLE_ASSISTANT, ROLE_USER, Role

__all__ = ["ROLE_ASSISTANT", "ROLE_USER", "Role"]
