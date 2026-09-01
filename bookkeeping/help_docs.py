"""Renders docs/user-guide/*.md for the in-app help section (see views/help.py).

The Markdown files in docs/user-guide/ are the single source of truth for the user guide;
this module only turns them into HTML for the browser. Chapter numbering, titles, and the
list of chapters are all derived from the files on disk so the in-app help never drifts out
of sync with docs/user-guide/README.md's own table of contents.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import markdown
from django.conf import settings
from markdown.extensions.toc import slugify_unicode

USER_GUIDE_DIR = Path(settings.BASE_DIR) / "docs" / "user-guide"

_CHAPTER_FILENAME_RE = re.compile(r"^\d{2}-.+\.md$")
_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_MD_LINK_RE = re.compile(r'href="(\d{2}-[a-z0-9-]+)\.md(#[^"]*)?"')

_MARKDOWN_EXTENSIONS = ["extra", "sane_lists", "toc"]
_MARKDOWN_EXTENSION_CONFIGS = {"toc": {"slugify": slugify_unicode}}


@dataclass(frozen=True)
class HelpChapter:
    slug: str
    title: str
    content: str


def _rewrite_chapter_links(html):
    return _MD_LINK_RE.sub(lambda m: f'href="/hjalp/{m.group(1)}/{m.group(2) or ""}"', html)


@lru_cache(maxsize=1)
def load_help_chapters():
    """All user-guide chapters, ordered by filename (README.md excluded)."""
    chapters = []
    for path in sorted(USER_GUIDE_DIR.glob("*.md")):
        if not _CHAPTER_FILENAME_RE.match(path.name):
            continue
        text = path.read_text(encoding="utf-8")
        title_match = _TITLE_RE.search(text)
        title = title_match.group(1) if title_match else path.stem
        html = markdown.markdown(
            text,
            extensions=_MARKDOWN_EXTENSIONS,
            extension_configs=_MARKDOWN_EXTENSION_CONFIGS,
        )
        chapters.append(HelpChapter(slug=path.stem, title=title, content=_rewrite_chapter_links(html)))
    return tuple(chapters)


def get_help_chapter(slug):
    for chapter in load_help_chapters():
        if chapter.slug == slug:
            return chapter
    return None
