"""6-босқич: ёрдам матнлари, видео рўйхати ва панелга ўтган қоидалар."""
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from jobs.guide import HELP, help_texts, videos, youtube_id

from . import conf
from .models import Event, SiteSetting

PASSWORD = "Kuchli-parol-2026"


@override_settings(NARXLASH_STAFF_2FA=False)
class HelpTextsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.boss = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="B", last_name="A")
        self.viewer = User.objects.create_user("v@test.uz", PASSWORD, first_name="V", last_name="V", is_staff=True)
        self.ali = User.objects.create_user("ali@test.uz", PASSWORD, first_name="Ali", last_name="V")

    def test_override_shows_everywhere_and_empty_means_default(self):
        self.client.force_login(self.boss)
        r = self.client.post(reverse("panel:help_texts_save"),
                             {"t_overlap_0": "Мослик (янги ном)", "t_overlap_1": "", "t_overlap_2": HELP["overlap"][2]})
        self.assertRedirects(r, reverse("panel:help_texts"))
        self.assertEqual(conf.raw("help_texts"), {"overlap": ["Мослик (янги ном)", "", ""]})  # асл матнга тенги — сақланмайди
        t = help_texts()["overlap"]
        self.assertEqual(t[0], "Мослик (янги ном)")
        self.assertEqual(t[1:], HELP["overlap"][1:])
        self.assertTrue(Event.objects.filter(kind=Event.Kind.ADMIN_SETTINGS, detail__contains="Ёрдам матнлари").exists())
        self.client.force_login(self.ali)
        self.assertContains(self.client.get(reverse("help")), "Мослик (янги ном)")
        # ҳаммаси бўшатилса — базадан ўчади
        self.client.force_login(self.boss)
        self.client.post(reverse("panel:help_texts_save"), {"t_overlap_0": "", "t_overlap_1": "", "t_overlap_2": ""})
        self.assertFalse(SiteSetting.objects.filter(key="help_texts").exists())
        self.assertEqual(help_texts()["overlap"], HELP["overlap"])

    def test_viewer_sees_but_cannot_save(self):
        self.client.force_login(self.viewer)
        self.assertContains(self.client.get(reverse("panel:help_texts")), HELP["overlap"][0])
        self.assertEqual(self.client.post(reverse("panel:help_texts_save"), {"t_overlap_0": "x"}).status_code, 403)
        self.client.force_login(self.ali)
        self.assertEqual(self.client.get(reverse("panel:help_texts")).status_code, 404)

    def test_videos(self):
        self.assertEqual(youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10"), "dQw4w9WgXcQ")
        self.assertEqual(youtube_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(youtube_id("https://example.com/video.mp4"), "")
        self.client.force_login(self.boss)
        url = reverse("panel:videos_save")
        bad = self.client.post(url, {"v_title": ["Хато"], "v_url": ["https://example.com/a"], "v_page": [""], "v_note": [""]}, follow=True)
        self.assertContains(bad, "YouTube ҳаволаси эмас")
        self.assertEqual(videos(), [])
        self.client.post(url, {"v_title": ["Юклаш", ""], "v_url": ["https://youtu.be/dQw4w9WgXcQ", ""],
                               "v_page": ["upload", ""], "v_note": ["<b>қисқа</b>", ""]})
        self.assertEqual(videos(), [{"title": "Юклаш", "id": "dQw4w9WgXcQ", "page": "upload", "note": "<b>қисқа</b>"}])
        self.client.force_login(self.ali)
        page = self.client.get(reverse("help"))
        self.assertContains(page, 'data-yt="dQw4w9WgXcQ"')
        self.assertContains(page, "&lt;b&gt;қисқа&lt;/b&gt;")  # изоҳ — оддий матн
        self.assertIn("https://www.youtube-nocookie.com", page.headers.get("Content-Security-Policy", ""))

    def test_video_button_only_on_its_page(self):
        """7-босқич: «▶ Видео» — фақат видеоси қўшилган саҳифада; YouTube тугма босилгандагина уланади."""
        conf.put("videos", [{"title": "Юклаш дарси", "id": "dQw4w9WgXcQ", "page": "upload", "note": ""}], self.boss)
        self.client.force_login(self.ali)
        up = self.client.get(reverse("index"), HTTP_REFERER="http://testserver/")
        self.assertContains(up, 'id="vid-btn"')
        self.assertContains(up, "dQw4w9WgXcQ")
        self.assertNotContains(up, "<iframe")  # олдиндан уланмайди
        self.assertNotContains(self.client.get(reverse("dashboard")), 'id="vid-btn"')

    def test_rules_moved_to_panel(self):
        self.assertEqual(conf.get("max_files"), 10)
        self.assertEqual(conf.get("lm_owners_min"), 2)
        conf.save({"max_files": 3}, self.boss)
        from jobs.services import UploadError, create_batch
        from django.core.files.uploadedfile import SimpleUploadedFile
        files = [SimpleUploadedFile(f"{i}.xlsx", b"x") for i in range(4)]
        with self.assertRaisesMessage(UploadError, "кўпи билан 3 та файл"):
            create_batch(self.ali, files)
