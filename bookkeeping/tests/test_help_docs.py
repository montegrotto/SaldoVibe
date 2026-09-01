import markdown
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape
from markdown.extensions.toc import slugify_unicode

from bookkeeping.help_docs import USER_GUIDE_DIR, load_help_chapters
from saldovibe.testing import create_user


class HelpDocsRenderingTests(TestCase):
    def test_slugify_keeps_swedish_characters(self):
        html = markdown.markdown(
            "## Räkenskapsår",
            extensions=["toc"],
            extension_configs={"toc": {"slugify": slugify_unicode}},
        )
        self.assertIn('id="räkenskapsår"', html)

    def test_loads_every_chapter_file_on_disk(self):
        expected_slugs = {path.stem for path in USER_GUIDE_DIR.glob("*.md") if path.stem[:2].isdigit()}
        loaded_slugs = {chapter.slug for chapter in load_help_chapters()}
        self.assertEqual(expected_slugs, loaded_slugs)

    def test_internal_md_links_are_rewritten_to_app_urls(self):
        chapter = next(c for c in load_help_chapters() if c.slug == "01-komma-igang")
        self.assertIn('href="/hjalp/02-lopande-bokforing/"', chapter.content)


class HelpViewTests(TestCase):
    def setUp(self):
        self.user = create_user("help-user@example.com")

    def test_index_requires_login(self):
        response = self.client.get(reverse("bookkeeping:help_index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_index_lists_every_chapter(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("bookkeeping:help_index"))

        self.assertEqual(response.status_code, 200)
        for chapter in load_help_chapters():
            self.assertContains(response, escape(chapter.title))

    def test_chapter_renders_for_every_known_slug(self):
        self.client.force_login(self.user)
        for chapter in load_help_chapters():
            with self.subTest(slug=chapter.slug):
                response = self.client.get(reverse("bookkeeping:help_chapter", args=[chapter.slug]))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, escape(chapter.title))

    def test_unknown_slug_is_404(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("bookkeeping:help_chapter", args=["99-finns-inte"]))
        self.assertEqual(response.status_code, 404)

    def test_first_chapter_has_no_previous_link_but_has_next(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("bookkeeping:help_chapter", args=["01-komma-igang"]))

        self.assertContains(response, "2. Löpande bokföring")
