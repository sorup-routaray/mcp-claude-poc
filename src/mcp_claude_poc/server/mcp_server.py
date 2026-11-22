from inspect import cleandoc
from typing import Annotated

from pydantic import Field
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.server.mcpserver.prompts import base

mcp = MCPServer("DocumentMCP", log_level="ERROR")

docs = {
    "deposition.md": "This deposition covers the testimony of Angela Smith, P.E.",
    "report.pdf": "The report details the state of a 20m condenser tower.",
    "financials.docx": "These financials outline the project's budget and expenditures.",
    "outlook.pdf": "This document presents the projected future performance of the system.",
    "plan.md": "The plan outlines the steps for the project's implementation.",
    "spec.txt": "These specifications define the technical requirements for the equipment.",
}


# ---------- Tools ----------
# mcp 2.x: raise ToolError for expected failures. Its message is returned to the
# model as an is_error result; any other exception is logged as a server crash
# and the model only sees a generic "Error executing tool" message.

@mcp.tool(
    name="read_doc",
    description="Read the contents of a document by its ID.",
)
def read_doc(
    doc_id: Annotated[str, Field(description="The ID of the document to read.")],
) -> str:
    if doc_id not in docs:
        raise ToolError(f"Document with ID '{doc_id}' not found.")
    return docs[doc_id]


@mcp.tool(
    name="write_doc",
    description="Edit a document by replacing old_string with new_string.",
)
def write_doc(
    doc_id: Annotated[str, Field(description="The ID of the document to edit.")],
    old_string: Annotated[str, Field(description="The exact text to replace.")],
    new_string: Annotated[str, Field(description="The text to put in its place.")],
) -> str:
    if doc_id not in docs:
        raise ToolError(f"Document with ID '{doc_id}' not found.")
    if old_string not in docs[doc_id]:
        raise ToolError(f"old_string was not found in '{doc_id}'.")
    docs[doc_id] = docs[doc_id].replace(old_string, new_string)
    return f"Document '{doc_id}' updated."


# ---------- Resources ----------

@mcp.resource(
    "docs://documents",
    mime_type="application/json",
    description="List all document IDs available in the system.",
)
def list_docs() -> list[str]:
    return list(docs.keys())


@mcp.resource(
    "docs://documents/{doc_id}",
    mime_type="text/plain",
    description="Get the contents of a document by its ID.",
)
def fetch_doc(doc_id: str) -> str:
    if doc_id not in docs:
        raise ResourceError(f"Document with ID '{doc_id}' not found.")
    return docs[doc_id]


# ---------- Prompts ----------

@mcp.prompt(
    name="format",
    description="Rewrite a document in markdown format.",
)
def format_document(
    doc_id: Annotated[str, Field(description="Id of the document to format")],
) -> list[base.Message]:
    prompt = cleandoc(f"""
        Your goal is to reformat a document so it is written in markdown syntax.

        The id of the document to reformat is:
        <document_id>
        {doc_id}
        </document_id>

        Steps:
        1. Read the document with the 'read_doc' tool.
        2. Restructure it using headers, bullet points, tables, etc. where they help.
           You may add light connecting text, but do not change the meaning or add new facts.
        3. Save the result with the 'write_doc' tool, using the full original text as
           old_string and the reformatted text as new_string.
        4. Reply with the final version of the document only. Do not explain your changes.

        If the document cannot be found, say so instead of making up content.
    """)
    return [base.UserMessage(prompt)]


@mcp.prompt(
    name="summarize",
    description="Summarize a document in a few bullet points.",
)
def summarize_document(
    doc_id: Annotated[str, Field(description="Id of the document to summarize")],
) -> list[base.Message]:
    prompt = cleandoc(f"""
        Summarize the document with this id in 3 short bullet points:
        <document_id>
        {doc_id}
        </document_id>

        Read it with the 'read_doc' tool first. Do not edit the document.
        If it cannot be found, say so instead of making up content.
    """)
    return [base.UserMessage(prompt)]


def main() -> None:
    """Run the server over stdio (console script: mcp-claude-poc-server)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
