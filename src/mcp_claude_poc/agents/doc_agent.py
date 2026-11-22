import re
from typing import Any

from mcp.types import Prompt, PromptMessage

# Finds "@doc_id" mentions in a question. The ID is the run of letters,
# digits, "." , "_" and "-" right after "@", so other punctuation around it
# isn't part of the ID: "(@plan.md)", "@plan.md," and "@plan.md?" all give
# "plan.md". (?<![\w@]) skips an "@" inside a word, so an email address such
# as a@b.com isn't treated as a mention.
MENTION_RE = re.compile(r"(?<![\w@])@([\w.\-]+)")

from mcp_claude_poc.client.mcp_client import MCPClient
from mcp_claude_poc.agents.base_agent import Agent
from mcp_claude_poc.core.claude_client import ClaudeClient


class DocAgent(Agent):
    """
    Document agent: the Agent tool-use loop plus document features.
    Handles commands, document mentions, and prompt execution.
    """
    def __init__(
        self,
        doc_client: MCPClient,
        clients: dict[str, MCPClient],
        claude_client: ClaudeClient,
    ):
        super().__init__(clients=clients, claude_client=claude_client)
        self.doc_client: MCPClient = doc_client

    # Prompts exposed by the document server (used for /commands)
    async def list_prompts(self) -> list[Prompt]:
        return await self.doc_client.list_prompts()

    # IDs of every document (docs://documents resource). Used by the CLI for
    # @-completion; it raises if the server call fails.
    async def list_docs_ids(self) -> list[str]:
        return await self.doc_client.read_resource("docs://documents")

    # Same as list_docs_ids(), but never raises and always returns a list of
    # strings. A question must still work when the document list can't be
    # read, so failures fall back to "no known documents":
    #   - read_resource() returns None when the resource has no contents
    #     (", ".join(None) would raise TypeError)
    #   - a non-JSON resource comes back as a plain str (join would split it
    #     into single characters)
    #   - the server call itself can fail (MCPError, dead subprocess, ...)
    async def _safe_docs_ids(self) -> list[str]:
        try:
            ids = await self.list_docs_ids()
        except Exception:
            # Broad on purpose: the list is optional context for the prompt,
            # so no failure here should block the user's question.
            return []
        if not isinstance(ids, list):
            return []
        return [str(doc_id) for doc_id in ids]

    # Content of one document (docs://documents/{doc_id} resource)
    async def get_doc_content(self, doc_id: str) -> str:
        return await self.doc_client.read_resource(f"docs://documents/{doc_id}")

    async def get_prompt(self, command: str, doc_id: str) -> list[PromptMessage]:
        return await self.doc_client.get_prompt(command, {"doc_id": doc_id})

    # Inline the content of every @mentioned document. `doc_ids` is passed in
    # by _process_query() so the document list is fetched once per question
    # (it used to be fetched here and again for the prompt).
    async def _extract_resources(self, query: str, doc_ids: list[str]) -> str:
        # Dots are valid inside IDs ("report.pdf"), so the regex also captures
        # a sentence-ending "." ("Summarize @report.pdf."). Strip trailing
        # "." and "-" so that still matches the document "report.pdf".
        mentions = {m.rstrip(".-") for m in MENTION_RE.findall(query)}
        if not mentions:
            return ""

        mentioned_docs: list[tuple[str, str]] = []
        for doc_id in doc_ids:
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

        # Fetch the document list once and reuse it for both the @mentions and
        # the list shown to Claude. An empty list means "unknown"; Claude can
        # still answer, or look documents up with the read_doc tool.
        doc_ids = await self._safe_docs_ids()
        added_resources = await self._extract_resources(query, doc_ids)
        doc_list = ", ".join(doc_ids) if doc_ids else "(unavailable)"

        prompt = f"""The user has a question:
        <query>
        {query}
        </query>
        The available document IDs are: {doc_list}
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
