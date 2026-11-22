"""Build the GitHub Wiki pages from README.md (and optionally publish them).

The wiki is a copy of the README split into one page per topic, so the README
stays the single source of truth: edit the README, then rerun this script.

Usage (from the project root):

    uv run python scripts/build_wiki.py            # write pages to ../mcp-claude-poc.wiki
    uv run python scripts/build_wiki.py --push     # also commit + push them to the wiki
    uv run python scripts/build_wiki.py --out DIR  # write pages somewhere else

--push needs the wiki to exist: enable Wikis in the repo settings and create
the first page once in the GitHub web UI (GitHub only creates the wiki's git
repository then).
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/sorup-routaray/mcp-claude-poc"
WIKI_GIT_URL = f"{REPO_URL}.wiki.git"

# Wiki page -> README headings it is cut from: (first heading, heading where
# it stops). None as the end means "to the end of the README". A page made of
# several README sections lists several ranges.
PAGES: dict[str, list[tuple[str, str | None]]] = {
    "Getting-Started": [("## Quickstart", "## Agent"), ("## Requirements", "## Configuration")],
    "Agent": [("## Agent", "### How Claude chooses a tool")],
    "How-Claude-Chooses-a-Tool": [("### How Claude chooses a tool", "## Requirements")],
    "Configuration": [("## Configuration", "## Usage")],
    "Usage": [("## Usage", "### How `@` completion finds document names")],
    "At-Mention-Completion": [("### How `@` completion finds document names", "## Run each component")],
    "Running-Components": [("## Run each component", "## Testing")],
    "Testing": [("## Testing", "## Troubleshooting")],
    "Troubleshooting": [("## Troubleshooting", "## Design notes")],
    "Design-and-Layout": [("## Design notes", None)],
}

# One-line descriptions for the index table on the Home page.
DESCRIPTIONS = {
    "Getting-Started": "Quickstart and requirements",
    "Agent": "DocAgent, Agent loop and ClaudeClient",
    "How-Claude-Chooses-a-Tool": "Tool menu, tool_use, routing, tool_result",
    "Configuration": ".env variables and command-line flags",
    "Usage": "Running the chat, @mentions, /commands",
    "At-Mention-Completion": "How @ completion finds document names",
    "Running-Components": "Chat app, server, client smoke test, Inspector",
    "Testing": "Smoke test and MCP Inspector",
    "Troubleshooting": "Common errors and fixes",
    "Design-and-Layout": "Design notes and project layout",
}

# README anchor -> where that heading lives in the wiki ("Page" or
# "Page#anchor"). README links like [x](#agent) are rewritten with this map,
# because each wiki page is a separate URL. Add an entry when the README gets
# a new linked heading; unknown anchors are reported by the script.
ANCHORS = {
    "quickstart": "Getting-Started#quickstart",
    "requirements": "Getting-Started#requirements",
    "without-uv-plain-pip": "Getting-Started#without-uv-plain-pip",
    "agent": "Agent",
    "claudeclient": "Agent#claudeclient",
    "agent-loop-agentrun": "Agent#agent-loop-agentrun",
    "how-claude-chooses-a-tool": "How-Claude-Chooses-a-Tool",
    "configuration": "Configuration",
    "usage": "Usage",
    "how--completion-finds-document-names": "At-Mention-Completion",
    "run-each-component": "Running-Components",
    "1-chat-app-client--claude--server": "Running-Components#1-chat-app-client--claude--server",
    "2-mcp-server-only": "Running-Components#2-mcp-server-only",
    "3-mcp-client-smoke-test": "Running-Components#3-mcp-client-smoke-test",
    "4-mcp-inspector": "Running-Components#4-mcp-inspector",
    "run-the-server-alone": "Running-Components#run-the-server-alone",
    "testing": "Testing",
    "client-smoke-test-no-claude-or-api-key-needed": "Testing#client-smoke-test-no-claude-or-api-key-needed",
    "mcp-inspector-browser-ui": "Testing#mcp-inspector-browser-ui",
    "troubleshooting": "Troubleshooting",
    "design-notes": "Design-and-Layout#design-notes",
    "project-layout": "Design-and-Layout#project-layout",
}

LINK_RE = re.compile(r"\]\(#([^)]+)\)")


def cut(lines: list[str], start: str, end: str | None) -> str:
    """Return the README lines from heading `start` up to heading `end`."""
    try:
        s = lines.index(start)
        e = lines.index(end) if end else len(lines)
    except ValueError as exc:
        # A heading was renamed in the README: update PAGES above.
        sys.exit(f"README heading not found: {exc}. Update PAGES in {Path(__file__).name}.")
    return "\n".join(lines[s:e]).strip()


def promote_headings(text: str) -> str:
    """If a page starts at a '###' heading, move all its headings up one level."""
    if not text.startswith("### "):
        return text
    return re.sub(r"^#(#+) ", r"\1 ", text, flags=re.M)


def fix_links(text: str, page: str, unknown: set[str]) -> str:
    """Rewrite README '#anchor' links to point at the right wiki page."""
    def repl(match: re.Match) -> str:
        anchor = match.group(1)
        target = ANCHORS.get(anchor)
        if target is None:
            unknown.add(anchor)
            return match.group(0)
        target_page, _, target_anchor = target.partition("#")
        if target_page == page:
            # Same page: keep a plain anchor so the link scrolls, not reloads.
            return f"](#{target_anchor})" if target_anchor else match.group(0)
        return f"]({target})"
    return LINK_RE.sub(repl, text)


def build(readme: Path, out: Path) -> list[str]:
    """Write all wiki pages into `out`; return the file names written."""
    lines = readme.read_text(encoding="utf-8").splitlines()

    pages = {
        name: promote_headings("\n\n".join(cut(lines, s, e) for s, e in ranges))
        for name, ranges in PAGES.items()
    }

    # Home: the README intro (everything above "## Contents") plus a page index.
    intro = cut(lines, "# mcp-claude-poc", "## Contents").removeprefix("# mcp-claude-poc").strip()
    index = "\n".join(f"| [{n.replace('-', ' ')}]({n}) | {DESCRIPTIONS[n]} |" for n in PAGES)
    pages = {
        "Home": (
            f"# mcp-claude-poc wiki\n\n{intro}\n\n## Pages\n\n"
            f"| Page | What it covers |\n| --- | --- |\n{index}\n\n"
            "> Generated from the project's `README.md` by `scripts/build_wiki.py`. "
            "Edit the README, then rerun the script; edits made here are overwritten."
        ),
        **pages,
    }

    unknown: set[str] = set()
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in pages.items():
        (out / f"{name}.md").write_text(fix_links(text, name, unknown) + "\n", encoding="utf-8", newline="\n")
        written.append(f"{name}.md")

    # Shown on every wiki page.
    sidebar = "**[Home](Home)**\n\n" + "\n".join(f"- [{n.replace('-', ' ')}]({n})" for n in PAGES) + "\n"
    footer = f"[Repository]({REPO_URL}) · [README]({REPO_URL}#readme)\n"
    (out / "_Sidebar.md").write_text(sidebar, encoding="utf-8", newline="\n")
    (out / "_Footer.md").write_text(footer, encoding="utf-8", newline="\n")
    written += ["_Sidebar.md", "_Footer.md"]

    if unknown:
        print(f"Warning: links to unknown anchors left unchanged: {sorted(unknown)}. "
              "Add them to ANCHORS.", file=sys.stderr)
    return written


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)


def prepare_clone(out: Path) -> None:
    """Make `out` an up-to-date clone of the wiki repository."""
    if (out / ".git").is_dir():
        result = git("pull", "--ff-only", cwd=out)
    else:
        if out.exists() and any(out.iterdir()):
            sys.exit(f"{out} exists but is not a git clone of the wiki. Empty it or pass --out.")
        result = git("clone", WIKI_GIT_URL, str(out), cwd=PROJECT_ROOT)
    if result.returncode != 0:
        sys.exit(
            f"git failed:\n{result.stderr.strip()}\n\n"
            "If the wiki doesn't exist yet: enable Wikis in the repo settings and "
            "create the first page in the GitHub web UI, then rerun."
        )


def publish(out: Path) -> None:
    """Commit the generated pages and push them to the wiki."""
    git("add", "-A", cwd=out)
    if git("diff", "--cached", "--quiet", cwd=out).returncode == 0:
        print("Wiki is already up to date; nothing to push.")
        return
    commit = git("commit", "-m", "Update wiki from README.md", cwd=out)
    if commit.returncode != 0:
        sys.exit(f"git commit failed:\n{commit.stderr.strip()}")
    push = git("push", cwd=out)
    if push.returncode != 0:
        sys.exit(f"git push failed:\n{push.stderr.strip()}")
    print(f"Pushed. View it at {REPO_URL}/wiki")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--readme", type=Path, default=PROJECT_ROOT / "README.md")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT.parent / "mcp-claude-poc.wiki",
                        help="output folder (with --push: the local clone of the wiki)")
    parser.add_argument("--push", action="store_true", help="commit and push the pages to the wiki")
    args = parser.parse_args()

    out = args.out.resolve()
    if args.push:
        prepare_clone(out)
    written = build(args.readme, out)
    print(f"Wrote {len(written)} files to {out}")
    if args.push:
        publish(out)


if __name__ == "__main__":
    main()
