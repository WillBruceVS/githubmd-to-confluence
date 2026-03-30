"""
Module to import GitHub README documents into Atlassian Confluence
Fully updated version with:
- robust fenced code block parsing
- admonition → Confluence info panels
- ordered list normalization
- folder-ID handling
- image macro fixes
- cleaned directory walker
"""

# mypy: disable-error-code="import-untyped,attr-defined,no-untyped-call"

import os
import re
import uuid
import mimetypes
import argparse
from typing import Any, Optional
import markdown2
from atlassian import Confluence

# ---------------------------------------------------------
# ARGUMENT PARSER
# ---------------------------------------------------------

parser = argparse.ArgumentParser(description="Sync Markdown to Confluence")
parser.add_argument(
    "--root-page-id", required=True, help="Root Confluence page ID"
)
parser.add_argument(
    "--root-dir", default=".", help="Filesystem root directory"
)
parser.add_argument("--space", default=".", help="Confluence Space to use")
args = parser.parse_args()

ROOT_PARENT_PAGE_ID = args.root_page_id
ROOT_DIR = args.root_dir

# ---------------------------------------------------------
# CONNECT TO CONFLUENCE
# ---------------------------------------------------------

confluence = Confluence(
    url=os.environ["CONFLUENCE_URL"],
    username=os.environ["CONFLUENCE_EMAIL"],
    password=os.environ["CONFLUENCE_API_TOKEN"],
)

SPACE = args.space

# ---------------------------------------------------------
# CHECK ROOT PAGE TYPE (FOLDER → CREATE REAL PAGE)
# ---------------------------------------------------------

root_obj = confluence.get_page_by_id(ROOT_PARENT_PAGE_ID, expand="type")

if root_obj.get("type") == "folder":
    print("📁 Provided ID is a folder — creating real root page inside it…")
    new_page = confluence.create_page(
        space=SPACE,
        title="Documentation Root",
        parent_id=ROOT_PARENT_PAGE_ID,
        body="<p>Initializing…</p>",
    )
    ROOT_PARENT_PAGE_ID = new_page["id"]
    ROOT_PAGE_TITLE = "Documentation Root"
else:
    ROOT_PAGE_TITLE = root_obj.get("title", "Root")

IGNORED_DIRS = {
    "__pycache__",
    ".venv",
    "venv",
    ".terraform",
    ".git",
    "generated",
    ".idea",
    ".vscode",
    "node_modules",
}

# ============================================================================
# UTILITIES
# ============================================================================


def get_or_create_page(title: str, parent_id: Optional[str]) -> Any:
    """
    Used to lookup a Confluence page, or create it if it does not exist already

    :param title: Title of the page
    :type title: str
    :param parent_id: Title of parent page
    :type parent_id: Optional[str]
    :return: ID of the created page
    :rtype: Any
    """
    page = confluence.get_page_by_title(SPACE, title)
    if page:
        return page["id"]

    new_page_to_create = confluence.create_page(
        space=SPACE,
        title=title,
        parent_id=parent_id,
        body="<p>Initializing…</p>",
    )
    print(f"📘 Created page: {title}")
    return new_page_to_create["id"]


# ============================================================================
# IMAGE UPLOAD
# ============================================================================


def upload_image_as_attachment(page_id: str, image_path: str) -> str:
    """
    Uploads an image as an attachment for a page

    :param page_id: ID for the page
    :type page_id: str
    :param image_path: Path of the image to upload
    :type image_path: str
    :return: Filename of the uploaded attachment
    :rtype: str
    """
    filename = os.path.basename(image_path)
    mime = mimetypes.guess_type(image_path)[0] or "application/octet-stream"

    existing = confluence.get_attachments_from_content(page_id)
    names = [a["title"] for a in existing.get("results", [])]

    if filename in names:
        confluence.delete_attachment(page_id=page_id, filename=filename)

    print(f"📎 Uploading {filename} → page {page_id}")

    confluence.attach_file(
        filename=image_path,
        name=filename,
        content_type=mime,
        page_id=page_id,
        space=SPACE,
        comment="Uploaded via automation",
    )

    return filename


# ============================================================================
# ADMONITION PANEL HANDLING
# ============================================================================

admonitions: dict[str, str] = {}


def convert_admonitions(md: str) -> str:
    """
    Convert the blocks of GitHub markdown of "notes" to Confluence "info" blocks

    :param md: GitHub markdown to extract from
    :type md: str
    :return: Extracted block
    :rtype: str
    """
    macro_map = {
        "IMPORTANT": "warning",
        "WARNING": "warning",
        "CAUTION": "warning",
        "DANGER": "warning",
        "NOTE": "note",
        "INFO": "info",
        "TIP": "tip",
    }

    # Pattern A — [IMPORTANT]
    # Pattern A — [IMPORTANT]
    pattern1 = re.compile(
        r"""
        ^\[(IMPORTANT|WARNING|CAUTION|DANGER|NOTE|INFO|TIP)\]   # [IMPORTANT]
        \s*\n                                                   # newline
        (                                                       # start body
            (?:
                [ ]{0,4}.*\S.*\n                                # normal text
            )+
        )                                                       # end body
        (?=\n|$)                                                # stop at blank line or EOF
        """,
        re.VERBOSE | re.MULTILINE,
    )

    def repl1(m):
        label = m.group(1)
        body = m.group(2).strip()
        macro = macro_map[label]
        marker = f"<!--ADMON_{uuid.uuid4().hex}-->"
        admonitions[marker] = (
            f'<ac:structured-macro ac:name="{macro}">'
            f'<ac:parameter ac:name="title">{label}</ac:parameter>'
            f"<ac:rich-text-body>{body}</ac:rich-text-body>"
            f"</ac:structured-macro>"
        )
        return marker

    md = pattern1.sub(repl1, md)
    return md


# ============================================================================
# ROBUST FENCED CODE BLOCK EXTRACTION
# ============================================================================


def extract_fenced_blocks(
    md: str,
) -> tuple[str, dict[str, tuple[str, str]]]:
    """
    Extracts "fenced" blocks in GitHub markdown

    :param md: GitHub markdown to extract from
    :type md: str
    :return: Extracted block
    :rtype:tuple[str, dict[str, tuple[str, str]]]
    """
    blocks: dict[str, tuple[str, str]] = {}

    fenced = re.compile(
        r"(^|\n)([ \t]*)(`{3,})\s*([\w+-]*)\s*\n(.*?)\n\2\3[ \t]*",
        re.DOTALL | re.VERBOSE,
    )

    def repl(m):
        lang = m.group(4) or "none"
        code = m.group(5)
        marker = f"<!--CODEBLOCK_{uuid.uuid4().hex}-->"
        blocks[marker] = (lang, code)
        return "\n" + marker + "\n"

    md = fenced.sub(repl, md)
    return md, blocks


# ============================================================================
# RESTORE CODE BLOCKS INTO CONFLUENCE
# ============================================================================


def restore_code_blocks(
    html: str, blocks: dict[str, tuple[Any | str, Any]]
) -> str:
    """
    Structure the extracted "fenced" blocks into Confluence markdown

    :param html: Body of code to structure within
    :type html: str
    :param blocks: Extracted code blocks
    :type blocks: dict[str, tuple[Any | str, Any]]
    :return: Body of markdown
    :rtype: str
    """
    for marker, (lang, code) in blocks.items():
        macro = (
            f'<ac:structured-macro ac:name="code">'
            f'<ac:parameter ac:name="language">{lang}</ac:parameter>'
            f"<ac:plain-text-body><![CDATA[{code}]]></ac:plain-text-body>"
            f"</ac:structured-macro>"
        )
        html = html.replace(marker, macro)
    return html


# ============================================================================
# MARKDOWN → HTML PIPELINE
# ============================================================================


def convert_markdown_with_images(md_path: str, page_id: str) -> str:
    """
    Converts GitHub image markdown into Confluence markdown

    :param md_path: Path to the markdown file to use
    :type md_path: str
    :param page_id: Page ID to insert the image attachment into
    :type page_id: str
    :return: Body of markdown
    :rtype: str
    """
    with open(md_path, "r", encoding="utf-8") as f:
        md = f.read()

    # Normalize GitHub 0. lists
    md = re.sub(r"(?m)^\s*0\.\s+", "1. ", md)

    # Convert admonitions first
    md = convert_admonitions(md)

    # Extract fenced code blocks
    md, blocks = extract_fenced_blocks(md)

    # IMAGE HANDLING
    def normalize_path(p: str) -> str:
        p = p.split("?")[0]
        p = os.path.normpath(p)
        direct = os.path.join(os.path.dirname(md_path), p)
        if os.path.exists(direct):
            return direct
        return os.path.join(ROOT_DIR, p.lstrip("./"))

    def img_repl(match):
        alt = match.group(1)
        rel_path = match.group(2)
        img_path = normalize_path(rel_path)

        if not os.path.exists(img_path):
            print(f"⚠️ Missing image: {rel_path}")
            return match.group(0)

        filename = upload_image_as_attachment(page_id, img_path)
        filename_safe = filename.replace("_", "\\_")

        return (
            f'<ac:image ac:alt="{alt}" ac:width="1200">'
            f'<ri:attachment ri:filename="{filename_safe}" />'
            f"</ac:image>"
        )

    md = re.sub(r"!\[(.*?)\]\((.*?)\)", img_repl, md)

    # Markdown → HTML
    html = markdown2.markdown(
        md,
        extras=["tables", "fenced-code-blocks", "code-friendly"],
    )

    # Restore code blocks
    html = restore_code_blocks(html, blocks)

    # Restore admonitions
    for marker, macro_html in admonitions.items():
        html = html.replace(marker, macro_html)

    return html


# ============================================================================
# DIRECTORY WALKER
# ============================================================================


def process_directory(path: str, parent_page_id: str | None):
    """
    Loop to process through directories looking for GitHub markdown files

    :param path: Path to scan through
    :type path: str
    :param parent_page_id: Parent page ID to use, and create pages beneath
    :type parent_page_id: str | None
    """
    folder_name = os.path.basename(path.rstrip("/"))

    if path == ROOT_DIR:
        page_id = parent_page_id
        page_title = ROOT_PAGE_TITLE  # ✅ Keep original Confluence title

    else:
        page_id = get_or_create_page(folder_name, parent_page_id)
        page_title = folder_name  # ✅ Only use folder name for subpages

    # Process README
    readme = None
    for f in os.listdir(path):
        if f.lower() in ("readme.md", "readme"):
            readme = os.path.join(path, f)
            break

    if readme:
        print(f"\n📄 Updating page content → {folder_name}")
        html = convert_markdown_with_images(readme, page_id)

        confluence.update_page(
            page_id=page_id,
            title=page_title,
            body=html,
            representation="storage",
        )
        print(f"✅ Updated: {folder_name}")

    # Recurse into subdirectories
    for dirname in sorted(os.listdir(path)):
        if dirname in IGNORED_DIRS:
            continue

        sub = os.path.join(path, dirname)

        if os.path.isdir(sub) and not dirname.startswith("."):
            process_directory(sub, page_id)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("🚀 Starting Confluence documentation sync…")
    process_directory(ROOT_DIR, ROOT_PARENT_PAGE_ID)
    print("\n✅ Finished")
