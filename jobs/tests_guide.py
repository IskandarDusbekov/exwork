"""Йўриқнома: саҳифа қўлланмалари, «?» изоҳлари ва /help/ саҳифаси."""
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from accounts.models import User

from .guide import ALIASES, GUIDE_PAGES, HELP, HELP_GROUPS

PASSWORD = "Kuchli-parol-2026"


class GuideTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ali@test.uz", PASSWORD, first_name="Ali", last_name="V")
        self.client.force_login(self.user)

    def test_every_help_key_is_listed_once(self):
        listed = [k for _, keys in HELP_GROUPS for k in keys]
        self.assertEqual(sorted(listed), sorted(HELP))
        self.assertTrue(all(v in HELP for v in ALIASES.values()))

    def test_help_page_shows_everything(self):
        r = self.client.get(reverse("help"))
        self.assertEqual(r.status_code, 200)
        for title, what, todo in HELP.values():
            self.assertContains(r, escape(title))
            self.assertContains(r, escape(todo))

    def test_new_user_gets_page_guide_once(self):
        r = self.client.get(reverse("index"))
        self.assertContains(r, 'page: "upload"')
        self.assertContains(r, 'id="help-data"')
        self.assertContains(r, '<script id="guides-seen" type="application/json">[]</script>', html=False)
        self.assertEqual(self.client.post(reverse("guide_seen"), {"page": "upload"}).json(), {"ok": True})
        self.client.post(reverse("guide_seen"), {"page": "upload"})  # такрор — бир марта ёзилади
        self.user.refresh_from_db()
        self.assertEqual(self.user.guides_seen, ["upload"])
        self.assertContains(self.client.get(reverse("index")), '["upload"]')
        self.assertContains(self.client.get(reverse("dashboard")), 'page: "dashboard"')

    def test_guide_seen_rejects_unknown_page_and_get(self):
        self.assertEqual(self.client.post(reverse("guide_seen"), {"page": "../admin"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("guide_seen")).status_code, 405)
        self.user.refresh_from_db()
        self.assertEqual(self.user.guides_seen, [])

    def test_reset_shows_guides_again(self):
        self.user.guides_seen = list(GUIDE_PAGES)
        self.user.save()
        r = self.client.post(reverse("guide_seen"), {"reset": "1"})
        self.assertRedirects(r, reverse("index"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.guides_seen, [])

    def test_anonymous_has_no_guide(self):
        self.client.logout()
        r = self.client.get(reverse("login"))
        self.assertNotContains(r, "narxGuide")
        self.assertEqual(self.client.post(reverse("guide_seen"), {"page": "upload"}).status_code, 302)  # кириш саҳифасига

    def test_help_tag(self):
        t = Template('{% load narx %}{% help "ready" %}|{% help "NOT_FOUND" "x" %}|{% help "queued" %}|{% help "nope" %}|{% help "" %}')
        out = t.render(Context()).split("|")
        self.assertIn('data-help="awaiting"', out[0])
        self.assertIn('class="help-q x" data-help="not_found"', out[1])
        self.assertIn('data-help="working"', out[2])
        self.assertEqual(out[3:], ["", ""])
