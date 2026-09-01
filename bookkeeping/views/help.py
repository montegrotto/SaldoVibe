"""In-app rendering of the docs/user-guide/ user manual (see ../help_docs.py)."""

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from ..help_docs import get_help_chapter, load_help_chapters


@login_required
def help_index(request):
    return render(request, "bookkeeping/help_index.html", {"chapters": load_help_chapters()})


@login_required
def help_chapter(request, slug):
    chapters = load_help_chapters()
    chapter = get_help_chapter(slug)
    if chapter is None:
        raise Http404("Kapitlet finns inte.")

    index = chapters.index(chapter)
    previous_chapter = chapters[index - 1] if index > 0 else None
    next_chapter = chapters[index + 1] if index < len(chapters) - 1 else None

    return render(
        request,
        "bookkeeping/help_chapter.html",
        {
            "chapter": chapter,
            "previous_chapter": previous_chapter,
            "next_chapter": next_chapter,
        },
    )
