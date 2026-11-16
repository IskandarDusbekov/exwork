"""5-босқич: смета­чи созламалари саҳифаси."""
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from panel.models import Track

from .guide import GUIDE_PAGES
from .services import nf_auto_of, ui_prefs_of


class UserSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("st@test.uz", "Kuchli-parol-2026", first_name="Ali", last_name="V")
        self.client.force_login(self.user)
        self.url = reverse("user_settings")

    def test_page_shows_defaults(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="nf_mode" value="auto" checked')
        self.assertContains(r, 'name="tips" value="1" checked')
        self.assertNotContains(r, 'name="dense" value="1" checked')
        self.assertContains(r, 'name="guide" value="1" checked')  # янги смета­чи — йўриқнома ўзи очилади
        self.assertContains(r, 'href="%s"' % self.url)  # чап менюда

    def test_save_applies_everywhere(self):
        r = self.client.post(self.url, {"nf_mode": "manual", "dense": "1"}, follow=True)  # tips, guide — ўчирилди
        self.assertRedirects(r, self.url)
        self.assertContains(r, "Созламалар сақланди")
        self.assertFalse(nf_auto_of(self.user))
        self.assertEqual(ui_prefs_of(self.user), {"tips": False, "dense": True})
        self.user.refresh_from_db()
        self.assertEqual(set(self.user.guides_seen), set(GUIDE_PAGES))
        page = self.client.get(reverse("help"))
        self.assertContains(page, 'class="no-tips is-dense"')
        # юклаш саҳифаси — шу усул
        self.assertContains(self.client.get(reverse("index"), HTTP_REFERER="http://testserver/"), "Усул: Қўлда")

    def test_guide_back_on_resets_seen(self):
        self.user.guides_seen = list(GUIDE_PAGES)
        self.user.save()
        self.client.post(self.url, {"nf_mode": "auto", "tips": "1", "guide": "1"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.guides_seen, [])

    def test_unchanged_guide_keeps_seen_list(self):
        self.user.guides_seen = [GUIDE_PAGES[0]]
        self.user.save()
        self.client.post(self.url, {"nf_mode": "auto", "tips": "1", "guide": "1"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.guides_seen, [GUIDE_PAGES[0]])

    def test_tracks_what_changed(self):
        self.client.post(self.url, {"nf_mode": "auto", "tips": "1", "guide": "1", "dense": "1"})
        t = Track.objects.get(step="settings_save")
        self.assertEqual(t.detail, "dense")

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
