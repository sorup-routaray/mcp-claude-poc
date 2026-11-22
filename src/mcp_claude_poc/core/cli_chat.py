from typing import Any

from mcp.types import Prompt, PromptMessage

from mcp_claude_poc.client.mcp_client import MCPClient
from mcp_claude_poc.core.chat import Chat
from mcp_claude_poc.agents.claude_agent import ClaudeAgent


class CliChat(Chat):
    """
    CLI chat interface for interacting with the MCP agent (CustomClaude).
    Handles commands, document mentions, and prompt execution.
    """
    def __init__(
        self,
        doc_client: MCPClient,
        clients: dict[str, MCPClient],
        claude_service: ClaudeAgent,
    ):
        super().__init__(clients=clients, claude_service=claude_service)
        self.doc_client: MCPClient = doc_client

    # Prompts exposed by the document server (used for /commands)
    async def list_prompts(self) -> list[Prompt]:
        return await self.doc_client.list_prompts()

    # IDs of every document (docs://documents resource)
    async def list_docs_ids(self) -> list[str]:
        return await self.doc_client.read_resource("docs://documents")

    # Content of one document (docs://documents/{doc_id} resource)
    async def get_doc_content(self, doc_id: str) -> str:
        return await self.doc_client.read_resource(f"docs://documents/{doc_id}")

    async def get_prompt(self, command: str, doc_id: str) -> list[PromptMessage]:
        return await self.doc_client.get_prompt(command, {"doc_id": doc_id})

    # Inline the content of every @mentioned document
    async def _extract_resources(self, query: str) -> str:
        mentions = {word[1:] for word in query.split() if word.startswith("@")}
        if not mentions:
            return ""

        mentioned_docs: list[tuple[str, str]] = []
        for doc_id in await self.list_docs_ids():
            if doc_id in mentions:
                mentioned_docs.append((doc_id, await self.get_doc_content(doc_id)))

        return "".join(
            f'\n<document id="{doc_id}">\n{content}\n</document>\n'
            for doc_id, content in mentioned_docs
        )

    # "/<prompt> <doc_id>" runs an MCP prompt instead of a free-form question.
    async def _process_command(self, query: str) -> bool:
        if not query.startswith("/"):
            return False

        words = query.split()
        command = words[0][1:]
        if len(words) < 2:
            raise ValueError(f"Usage: /{command} <doc_id>")

        messages = await self.doc_client.get_prompt(command, {"doc_id": words[1]})
        self.messages += self.convert_prompt_messages_to_message_params(messages)
        return True

    def convert_prompt_messages_to_message_params(
        self, prompt_messages: list[PromptMessage]
    ) -> list[dict[str, Any]]:
        return [self.convert_prompt_message_to_message_param(m) for m in prompt_messages]

    @staticmethod
    def _text_of(item: Any) -> str | None:
        if isinstance(item, dict):
            return item.get("text", "") if item.get("type") == "text" else None
        return getattr(item, "text", "") if getattr(item, "type", None) == "text" else None

    def convert_prompt_message_to_message_param(self, prompt_message: PromptMessage) -> dict[str, Any]:
        role = "user" if prompt_message.role == "user" else "assistant"
        content = prompt_message.content

        if isinstance(content, list):
            blocks = [{"type": "text", "text": t} for t in map(self._text_of, content) if t is not None]
            if blocks:
                return {"role": role, "content": blocks}
        else:
            text = self._text_of(content)
            if text is not None:
                return {"role": role, "content": text}

        # Non-text prompt content (images, resources) isn't supported here.
        return {"role": role, "content": "(unsupported prompt content)"}

    # Build the user turn: the question plus any @mentioned documents.
    async def _process_query(self, query: str) -> None:
        if await self._process_command(query):
            return

        added_resources = await self._extract_resources(query)

        prompt = f"""The user has a question:
<query>
{query}
</query>

The following context may be useful in answering their question:
<context>
{added_resources}
</context>

The user's query may mention documents like "@report.docx". The "@" only marks the mention; \
the document's actual name is "report.docx". If a document's content is already included \
above, you don't need a tool to read it.
Answer the user's question directly and concisely, starting with the exact information they need. \
Don't refer to the provided context; just use it to inform your answer."""

        self.messages.append({"role": "user", "content": prompt})
