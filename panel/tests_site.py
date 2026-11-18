"""Очиқ сайт: бош саҳифа матнлари панелдан, блог (видео бўлимлари ва мақолалар), sitemap, хавфсизлик."""
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from jobs import site

from . import conf
from .models import Category, Post, SiteSetting

PASSWORD = "Kuchli-parol-2026"
VIDEOS = [{"title": "Юклаш дарси", "id": "dQw4w9WgXcQ", "page": "upload", "note": "Қисқа <b>изоҳ</b>", "added": "2026-11-17"},
          {"title": "Далолатнома", "id": "9bZkp7q19f0", "page": "texnazor", "note": "", "added": "2026-11-17"}]


@override_settings(NARXLASH_STAFF_2FA=False)
class SiteTests(TestCase):
    def setUp(self):
        cache.clear()
        self.boss = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="B", last_name="A")
        self.viewer = User.objects.create_user("v@test.uz", PASSWORD, first_name="V", last_name="V", is_staff=True)

    def test_landing_defaults_and_panel_override(self):
        r = self.client.get(reverse("landing"))
        self.assertContains(r, "бир неча сонияда")
        self.assertContains(r, "Буюртмачи ва давлат ташкилотлари")
        self.assertContains(r, '"@type": "FAQPage"')
        self.assertNotContains(r, 'id="lessons"')  # видео йўқ — бўлим чиқмайди

        self.client.force_login(self.boss)
        self.assertEqual(self.client.get(reverse("panel:site")).status_code, 200)
        data = {"f_hero_title_em": "<script>x</script> тез", "f_hero_lead": "",
                "r_faq_0": ["Янги савол?", ""], "r_faq_1": ["Жавоб.", ""]}
        self.assertRedirects(self.client.post(reverse("panel:landing_save"), data), reverse("panel:site"))
        stored = conf.raw("landing")
        self.assertEqual(stored["hero"], {"title_em": "<script>x</script> тез"})  # бўш — стандарт, сақланмайди
        self.assertEqual(stored["faq"]["rows"], [["Янги савол?", "Жавоб."]])
        self.client.logout()
        r = self.client.get(reverse("landing"))
        self.assertContains(r, "&lt;script&gt;x&lt;/script&gt; тез")  # экранланган
        self.assertNotContains(r, "<script>x</script>")
        self.assertContains(r, "Янги савол?")
        self.assertNotContains(r, "Ресурс нархлари файли (RES) нима?")

        self.client.force_login(self.boss)
        self.client.post(reverse("panel:landing_save"), {"reset": "1"})
        self.assertFalse(SiteSetting.objects.filter(key="landing").exists())

    def test_only_superuser_changes(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("panel:site")).status_code, 200)
        self.client.post(reverse("panel:landing_save"), {"f_hero_title_a": "Бузилди"})
        self.client.post(reverse("panel:blog_post_new"), {"title": "X", "body": "Y", "published": "1"})
        self.client.post(reverse("panel:blog_video_new"), {"url": "https://youtu.be/dQw4w9WgXcQ", "title": "V"})
        self.client.post(reverse("panel:blog_cat_save"), {"name": "Бўлим"})
        self.assertEqual(self.client.get(reverse("panel:blog")).status_code, 200)
        self.assertIsNone(conf.raw("videos"))
        self.assertFalse(Category.objects.exists())
        self.assertIsNone(conf.raw("landing"))
        self.assertFalse(Post.objects.exists())

    def test_blog_sections_video_page_and_sitemap(self):
        conf.put("videos", VIDEOS, self.boss)
        r = self.client.get(reverse("guide"))
        self.assertContains(r, 'id="v-upload"')
        self.assertContains(r, 'id="v-texnazor"')
        self.assertNotContains(r, 'id="v-smeta"')  # видеоси йўқ бўлим кўрсатилмайди
        r = self.client.get(reverse("blog_video", args=["dQw4w9WgXcQ"]))
        self.assertContains(r, '"@type": "VideoObject"')
        self.assertContains(r, "youtube-nocookie.com/embed/dQw4w9WgXcQ")
        self.assertContains(r, "Қисқа &lt;b&gt;изоҳ&lt;/b&gt;")
        self.assertEqual(self.client.get(reverse("blog_video", args=["xxxxxxxxxxx"])).status_code, 404)
        self.assertContains(self.client.get(reverse("landing")), 'id="lessons"')
        sm = self.client.get(reverse("sitemap_xml")).content.decode()
        self.assertIn("/blog/video/dQw4w9WgXcQ/", sm)
        self.assertIn("/blog/</loc>", sm)

    def test_post_create_publish_and_safe_body(self):
        self.client.force_login(self.boss)
        self.client.post(reverse("panel:blog_cat_save"), {"name": "Смета нархлаш"})
        cat = Category.objects.get()
        self.assertEqual(cat.slug, "smeta-narxlash")
        body = "Кириш.\n\n## Бўлим\n- биринчи\n- <img src=x onerror=alert(1)>"
        html = "<p>Кириш.</p><h2>Бўлим</h2><ul><li>биринчи</li><li><img src=x onerror=alert(1)></li></ul><script>x</script>"
        r = self.client.post(reverse("panel:blog_post_new"), {"title": "Смета қандай нархланади?", "body": html, "summary": "Қисқа",
                                                               "category": str(cat.pk)})
        p = Post.objects.get()
        self.assertRedirects(r, reverse("panel:blog_post_edit", args=[p.pk]))
        self.assertEqual((p.slug, p.published, p.category_id), ("smeta-qanday-narxlanadi", False, cat.pk))
        prev = self.client.post(reverse("panel:blog_preview"), {"body": body}).content.decode()
        self.assertIn("<h2>Бўлим</h2>", prev)
        self.assertNotIn("<img", prev)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("blog_post", args=[p.slug])).status_code, 404)  # қоралама
        self.assertEqual(self.client.get(reverse("blog_category", args=[cat.slug])).status_code, 404)  # чоп этилгани йўқ
        p.published = True
        p.save()
        r = self.client.get(reverse("blog_post", args=[p.slug]))
        self.assertContains(r, "<h2>Бўлим</h2>", html=True)
        self.assertNotContains(r, "<img src=x")  # муҳаррир HTML'и тозаланган: рухсатсиз расм ва скрипт йўқ
        self.assertNotContains(r, "<script>x")
        self.assertContains(r, '"@type": "Article"')
        self.assertContains(r, reverse("blog_category", args=[cat.slug]))
        self.assertContains(self.client.get(reverse("blog_category", args=[cat.slug])), "Смета қандай нархланади?")
        self.assertContains(self.client.get(reverse("blog")), '?c=smeta-narxlash')
        sm = self.client.get(reverse("sitemap_xml")).content.decode()
        self.assertIn(f"/blog/{p.slug}/", sm)
        self.assertIn("/blog/c/smeta-narxlash/", sm)
        # бир хил манзил ва банд сўз — сақланмайди, ёзилгани йўқолмайди
        self.client.force_login(self.boss)
        r = self.client.post(reverse("panel:blog_post_new"), {"title": "Смета қандай нархланади?", "body": "ёзилган матн"})
        self.assertContains(r, "ёзилган матн")
        self.client.post(reverse("panel:blog_post_new"), {"title": "Видео", "slug": "video", "body": "x"})
        self.assertEqual(Post.objects.count(), 1)
        self.client.post(reverse("panel:blog_cat_delete", args=[cat.pk]))
        p.refresh_from_db()
        self.assertIsNone(p.category)  # бўлим ўчди, мақола қолди
        self.client.post(reverse("panel:blog_post_delete", args=[p.pk]))
        self.assertFalse(Post.objects.exists())

    def test_video_add_edit_move_delete(self):
        self.client.force_login(self.boss)
        new = reverse("panel:blog_video_new")
        self.assertContains(self.client.post(new, {"url": "https://example.com/a", "title": "Хато"}), "YouTube ҳаволаси эмас")
        r = self.client.post(new, {"url": "https://youtu.be/dQw4w9WgXcQ", "title": "Юклаш", "page": "upload",
                                   "note": "Бу дарсда юклаш.\n\n## Қадамлар\n- папка\n- <script>x</script>"})
        self.assertRedirects(r, reverse("panel:blog_video_edit", args=["dQw4w9WgXcQ"]))
        self.client.post(new, {"url": "https://www.youtube.com/watch?v=9bZkp7q19f0", "title": "Иккинчи", "page": "smeta"})
        self.assertContains(self.client.post(new, {"url": "https://youtu.be/9bZkp7q19f0", "title": "Такрор"}), "аллақачон")
        self.assertEqual([v["id"] for v in conf.raw("videos")], ["dQw4w9WgXcQ", "9bZkp7q19f0"])
        self.client.post(reverse("panel:blog_video_move", args=["9bZkp7q19f0", "up"]))
        self.assertEqual([v["id"] for v in conf.raw("videos")], ["9bZkp7q19f0", "dQw4w9WgXcQ"])
        for u in (reverse("panel:blog") + "?tab=videos", reverse("panel:blog") + "?tab=cats", reverse("panel:blog"),
                  reverse("panel:blog_post_new"), reverse("panel:blog_video_new"),
                  reverse("panel:blog_video_edit", args=["9bZkp7q19f0"]), reverse("panel:help_texts"), reverse("panel:site")):
            self.assertEqual(self.client.get(u).status_code, 200, u)
        self.assertContains(self.client.get(reverse("panel:blog") + "?tab=videos"), "Иккинчи")
        self.client.post(reverse("panel:blog_video_edit", args=["9bZkp7q19f0"]),
                         {"url": "https://youtu.be/9bZkp7q19f0", "title": "Янги ном", "page": "smeta", "note": ""})
        self.assertEqual(conf.raw("videos")[0]["title"], "Янги ном")
        self.client.logout()
        page = self.client.get(reverse("blog_video", args=["dQw4w9WgXcQ"]))
        self.assertContains(page, "<h2>Қадамлар</h2>", html=True)
        self.assertContains(page, "&lt;script&gt;x&lt;/script&gt;")
        self.assertContains(page, "youtube.com/watch?v=dQw4w9WgXcQ")
        self.client.force_login(self.boss)
        self.client.post(reverse("panel:blog_video_delete", args=["dQw4w9WgXcQ"]))
        self.assertEqual([v["id"] for v in conf.raw("videos")], ["9bZkp7q19f0"])

    def test_slugify_and_body(self):
        self.assertEqual(site.slugify("Ўзбекча: Қўлланма ва ҒИШТ!"), "ozbekcha-qollanma-va-gisht")
        self.assertEqual(str(site.render_body("a\nb\n\nc")), "<p>a<br>b</p><p>c</p>")
