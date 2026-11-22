from typing import List, Optional
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.document import Document
from prompt_toolkit.buffer import Buffer

from mcp_claude_poc.agents.doc_agent import DocAgent


class CommandAutoSuggest(AutoSuggest):
    """
    Auto-suggests command arguments based on the available prompts.
    """
    def __init__(self, prompts: List):
        # Store the list of prompts and create a dictionary for quick lookup by name.
        self.prompts = prompts
        # Create a dictionary for quick lookup by name.
        self.prompt_dict = {prompt.name: prompt for prompt in prompts}

    def get_suggestion(
        self, buffer: Buffer, 
        document: Document
    ) -> Optional[Suggestion]:
        text = document.text

        if not text.startswith("/"):
            return None

        parts = text[1:].split()

        if len(parts) == 1:
            cmd = parts[0]

            prompt = self.prompt_dict.get(cmd)
            if prompt is not None and prompt.arguments:
                return Suggestion(f" {prompt.arguments[0].name}")

        return None


class UnifiedCompleter(Completer):
    """
    Completer that provides autocompletion for both commands and resources.
    """
    def __init__(self):
        # Initialize the list of prompts, prompt dictionary, and resources.
        self.prompts = []
        # Initialize the command autosuggester with the current prompts.
        self.auto_suggest = CommandAutoSuggest(self.prompts)
        # Initialize the prompt dictionary for quick lookup by name.
        self.prompt_dict = {}
        # Initialize the list of resources for autocompletion.
        self.resources = []

    def update_prompts(self, prompts: List):
        self.prompts = prompts
        self.prompt_dict = {prompt.name: prompt for prompt in prompts}

    def update_resources(self, resources: List):
        self.resources = resources

    def get_completions(self, document, complete_event):
        text = document.text
        text_before_cursor = document.text_before_cursor

        if "@" in text_before_cursor:
            last_at_pos = text_before_cursor.rfind("@")
            prefix = text_before_cursor[last_at_pos + 1 :]

            for resource_id in self.resources:
                if resource_id.lower().startswith(prefix.lower()):
                    yield Completion(
                        resource_id,
                        start_position=-len(prefix),
                        display=resource_id,
                        display_meta="Resource",
                    )
            return

        if text.startswith("/"):
            parts = text[1:].split()

            if len(parts) <= 1 and not text.endswith(" "):
                cmd_prefix = parts[0] if parts else ""

                for prompt in self.prompts:
                    if prompt.name.startswith(cmd_prefix):
                        yield Completion(
                            prompt.name,
                            start_position=-len(cmd_prefix),
                            display=f"/{prompt.name}",
                            display_meta=prompt.description or "",
                        )
                return

            if len(parts) == 1 and text.endswith(" "):
                cmd = parts[0]

                if cmd in self.prompt_dict:
                    for doc_id in self.resources:
                        yield Completion(
                            doc_id,
                            start_position=0,
                            display=doc_id,
                        )
                return

            if len(parts) >= 2:
                doc_prefix = parts[-1]

                # Resources are plain document-ID strings (docs://documents).
                for doc_id in self.resources:
                    if doc_id.lower().startswith(doc_prefix.lower()):
                        yield Completion(
                            doc_id,
                            start_position=-len(doc_prefix),
                            display=doc_id,
                        )
                return
            
class CliApp:
    """
    CLI application for interacting with the agent (DocAgent).

    Handles user input, autocompletion, and command execution.        
    """

    def __init__(self, agent: DocAgent):
        # Initialize the CLI application with the given agent.
        self.agent = agent
        # Initialize the resources for autocompletion.
        self.resources = []
        # Initialize the prompts for autocompletion.
        self.prompts = []
        # Initialize the completer for autocompletion.
        self.completer = UnifiedCompleter()
        # Initialize the command autosuggester for autocompletion.
        self.command_autosuggester = CommandAutoSuggest([])
        # Initialize the key bindings for the CLI application.
        self.kb = KeyBindings()

        # Define key binding for "/" to trigger autocompletion.
        @self.kb.add("/")
        def _(event):
            buffer = event.app.current_buffer
            if buffer.document.is_cursor_at_the_end and not buffer.text:
                buffer.insert_text("/")
                buffer.start_completion(select_first=False)
            else:
                buffer.insert_text("/")

        # Define key binding for "@" to trigger autocompletion.
        @self.kb.add("@")
        def _(event):
            buffer = event.app.current_buffer
            buffer.insert_text("@")
            if buffer.document.is_cursor_at_the_end:
                buffer.start_completion(select_first=False)

        # Define key binding for " " (space) to trigger autocompletion for commands.
        @self.kb.add(" ")
        def _(event):
            buffer = event.app.current_buffer
            text = buffer.text

            buffer.insert_text(" ")

            if text.startswith("/"):
                parts = text[1:].split()

                if len(parts) == 1:
                    buffer.start_completion(select_first=False)
                elif len(parts) == 2:
                    arg = parts[1]
                    if (
                        "doc" in arg.lower()
                        or "file" in arg.lower()
                        or "id" in arg.lower()
                    ):
                        buffer.start_completion(select_first=False)

        # Initialize the history and session for the CLI application.
        self.history = InMemoryHistory()
        # Initialize the session for the CLI application.
        self.session = PromptSession(
            # Configure the prompt session with completer, history, key bindings, and style.
            completer=self.completer,
            # Set the history for the session.
            history=self.history,
            # Set the key bindings for the session.
            key_bindings=self.kb,
            # Set the style for the session.
            style=Style.from_dict(
                {
                    "prompt": "#aaaaaa",
                    "completion-menu.completion": "bg:#222222 #ffffff",
                    "completion-menu.completion.current": "bg:#444444 #ffffff",
                }
            ),
            # Enable autocompletion while typing and in a separate thread.
            complete_while_typing=True,
            # Enable autocompletion in a separate thread.
            complete_in_thread=True,
            # Set the command autosuggester for the session.
            auto_suggest=self.command_autosuggester,
        )

    # Initialize the CLI app by refreshing resources and prompts.
    async def initialize(self):
        # Refresh the resources and prompts to initialize the CLI app.
        await self.refresh_resources()
        # Refresh the prompts after refreshing resources to ensure autocompletion is up-to-date.
        await self.refresh_prompts()

    # Refresh the list of available resources (documents) from the agent.
    async def refresh_resources(self):
        try:
            self.resources = await self.agent.list_docs_ids()
            self.completer.update_resources(self.resources)
        except Exception as e:
            print(f"Error refreshing resources: {e}")

    # Refresh the list of available prompts from the agent.
    async def refresh_prompts(self):
        try:
            # Fetch the list of available prompts from the agent.
            self.prompts = await self.agent.list_prompts()
            # Update the completer and autosuggester with the new prompts.
            self.completer.update_prompts(self.prompts)
            # Update the command autosuggester with the new prompts.
            self.command_autosuggester = CommandAutoSuggest(self.prompts)
            # Update the session's auto_suggest with the new command autosuggester.
            self.session.auto_suggest = self.command_autosuggester
        except Exception as e:
            print(f"Error refreshing prompts: {e}")

    # Run the interactive CLI loop, handling user input and agent responses.
    async def run(self):
        print("Ask a question, mention docs with @<doc_id>, or run /<prompt> <doc_id>. "
              "Ctrl+C or Ctrl+D to quit.")
        while True:
            try:
                user_input = await self.session.prompt_async("> ")
            except (KeyboardInterrupt, EOFError):
                break
            if not user_input.strip():
                continue
            try:
                response = await self.agent.run(user_input)
                print(f"\nResponse:\n{response}\n")
            except Exception as e:
                # One failed turn (API error, bad /command, ...) shouldn't end the session.
                print(f"Error: {e}")