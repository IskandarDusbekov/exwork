"""SEO: панелда сақлаш, очиқ саҳифалардаги мета-теглар, robots.txt, sitemap.xml, расм."""
import shutil
import tempfile
from pathlib import Path

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User

from . import seo
from .models import Event

PASSWORD = "Kuchli-parol-2026"
TMP = Path(tempfile.mkdtemp(prefix="narxlash_seo_"))
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def form(**kw):
    data = {f.key: seo.values()[f.key] for f in seo.FIELDS}
    data["index"] = "1"
    data.update(kw)
    return data


@override_settings(NARXLASH_STAFF_2FA=False, MEDIA_ROOT=TMP)
class SeoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        self.staff = User.objects.create_user("staff@test.uz", PASSWORD, first_name="S", last_name="T", is_staff=True)

    def save(self, **kw):
        self.client.force_login(self.admin)
        return self.client.post(reverse("panel:seo_save"), form(**kw), follow=True)

    def test_defaults_on_landing(self):
        page = self.client.get("/")
        self.assertContains(page, "<title>mhisobot — муҳандис ҳисобот")
        self.assertContains(page, '<link rel="canonical" href="http://testserver/">')
        self.assertContains(page, 'property="og:title"')
        self.assertContains(page, '"@type": "SoftwareApplication"')
        self.assertNotContains(page, "google-site-verification")
        self.assertNotContains(page, "googletagmanager")

    def test_save_codes_and_show_on_public_pages(self):
        r = self.save(google='<meta name="google-site-verification" content="AbC_123-xyz456" />',
                      yandex="1a2b3c4d5e6f7a8b", ga="g-abc1234", metrika="98 765 432", title="Смета нархлаш — mhisobot")
        self.assertContains(r, "Сақланди")
        v = seo.values()
        self.assertEqual((v["google"], v["ga"], v["metrika"]), ("AbC_123-xyz456", "G-ABC1234", "98765432"))
        self.assertTrue(Event.objects.filter(kind=Event.Kind.ADMIN_SETTINGS, detail__startswith="SEO:").exists())
        self.client.logout()
        home = self.client.get("/")
        self.assertContains(home, '<meta name="google-site-verification" content="AbC_123-xyz456">')
        self.assertContains(home, '<meta name="yandex-verification" content="1a2b3c4d5e6f7a8b">')
        self.assertContains(home, "<title>Смета нархлаш — mhisobot</title>")
        self.assertContains(home, "gtag/js?id=G-ABC1234")
        self.assertContains(home, "ym(98765432")
        login = self.client.get(reverse("login"))
        self.assertContains(login, "google-site-verification")
        self.assertContains(login, "<title>Кириш — mhisobot</title>")

    def test_bad_values_rejected(self):
        r = self.save(ga="UA-123")
        self.assertContains(r, "Google Analytics (GA4): нотўғри кўриниш")
        r = self.save(title="")
        self.assertContains(r, "бўш бўлмасин")
        r = self.save(yandex='"><script>alert(1)</script>')
        self.assertContains(r, "content=")
        self.assertEqual(seo.values()["yandex"], "")
        r = self.save(yandex="abc def ghi jkl")
        self.assertContains(r, "нотўғри кўриниш")

    def test_robots_and_sitemap(self):
        r = self.client.get("/robots.txt")
        self.assertEqual(r["Content-Type"], "text/plain; charset=utf-8")
        body = r.content.decode()
        self.assertIn("Allow: /", body)
        self.assertIn("Disallow: /object/", body)
        self.assertIn("Sitemap: http://testserver/sitemap.xml", body)
        from django.conf import settings
        self.assertNotIn(settings.PANEL_URL.strip("/"), body)  # панел манзили сир
        sm = self.client.get("/sitemap.xml").content.decode()
        self.assertIn("<loc>http://testserver/</loc>", sm)
        self.assertIn("<loc>http://testserver/login/</loc>", sm)

    def test_noindex_closes_everything(self):
        self.save(index="")
        self.client.logout()
        self.assertIn("Disallow: /\n", self.client.get("/robots.txt").content.decode())
        self.assertNotIn("<url>", self.client.get("/sitemap.xml").content.decode())
        self.assertContains(self.client.get("/"), "noindex, nofollow")

    def test_image_upload_and_serve(self):
        self.save(image=SimpleUploadedFile("og.png", PNG, content_type="image/png"))
        r = self.client.get("/og-image")
        self.assertEqual(r["Content-Type"], "image/png")
        r.close()
        self.client.logout()
        self.assertContains(self.client.get("/"), 'property="og:image" content="http://testserver/og-image"')
        r = self.save(image=SimpleUploadedFile("x.png", b"not an image"))
        self.assertContains(r, "Фақат PNG, JPG ёки WEBP")
        self.save(image_delete="1")
        self.assertEqual(self.client.get("/og-image").status_code, 404)

    def test_staff_sees_page_but_cannot_save(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("panel:seo"))
        self.assertContains(page, "Google натижасида шундай чиқади")
        self.assertEqual(self.client.post(reverse("panel:seo_save"), form()).status_code, 403)

    def test_internal_pages_have_no_analytics(self):
        self.save(ga="G-ABC1234")
        page = self.client.get(reverse("panel:seo"))
        self.assertNotContains(page, "gtag/js")
