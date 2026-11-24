"""«Биз ҳақимизда», «Шартлар», рўйхатдан ўтишда розилик, мақола муҳаррири (HTML тозалаш, расм) ва кўрилишлар сони."""
import shutil
import tempfile

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from panel import conf
from panel.models import Post, ViewCount

from .richtext import sanitize

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
UA = {"HTTP_USER_AGENT": "Mozilla/5.0 (Windows NT 10.0) Chrome/130"}
PW = "Kuchli-parol-2026"


class SanitizeTests(TestCase):
    def test_strips_dangerous(self):
        h = sanitize('<p onclick="x()" style="color:red;mso-x:1">a<script>alert(1)</script></p>'
                     '<a href="javascript:alert(1)">b</a><img src="http://evil/x.png"><iframe src="x"></iframe>'
                     '<p style="background:url(javascript:x)">c</p>')
        self.assertEqual(h, '<p style="color: red">a</p>b<p>c</p>')

    def test_keeps_formatting(self):
        img = "/img/202611/" + "a" * 32 + ".png"
        h = sanitize(f'<h1>T</h1><font size="5" face="Georgia">k</font><ol><li>1<li>2</ol>'
                     f'<figure style="width:50%"><img src="{img}" alt="x"><figcaption>c</figcaption></figure>'
                     f'<a href="https://x.uz">l</a>')
        self.assertIn("<h2>T</h2>", h)
        self.assertIn('<span style="font-size: 24px; font-family: Georgia, serif">k</span>', h)
        self.assertIn("<ol><li>1</li><li>2</li></ol>", h)
        self.assertIn(f'<figure style="width: 50%"><img src="{img}" alt="x" loading="lazy"><figcaption>c</figcaption></figure>', h)
        self.assertIn('rel="noopener nofollow"', h)

    def test_word_paste(self):
        h = sanitize('<!--[if gte mso 9]><xml><w:x/></xml><![endif]--><p class="MsoNormal"><span lang="UZ">Матн</span><o:p></o:p></p>')
        self.assertEqual(h, "<p>Матн</p>")


@override_settings(NARXLASH_SIGNUP="open")
class TermsTests(TestCase):
    def setUp(self):
        cache.clear()  # panel.conf кеши олдинги тестдан қолмасин

    def _reg(self, **over):
        return self.client.post(reverse("register"), {"first_name": "A", "last_name": "B", "email": "t@mail.uz",
                                                      "password1": PW, "password2": PW, **over})

    def test_guide_and_blog_split(self):
        Post.objects.create(title="Мақола бир", slug="m1", body="x", published=True)
        g = self.client.get(reverse("guide"))
        self.assertContains(g, "Видео қўлланма")
        self.assertNotContains(g, "Мақола бир")  # мақолалар — блогда
        self.assertContains(self.client.get(reverse("blog")), "Мақола бир")
        self.assertContains(self.client.get(reverse("register")), "та мақола")  # рўйхатдан ўтишда кичик эълон
        sm = self.client.get(reverse("sitemap_xml")).content.decode()
        for name in ("guide", "about_us", "terms"):
            self.assertIn(reverse(name), sm)
        self.assertIn("/blog/m1/</loc><lastmod>", sm)  # мақола — ўз санаси билан

    def test_checkbox_required(self):
        page = self.client.get(reverse("register"))
        self.assertContains(page, 'name="agree"')
        self.assertContains(self._reg(), "шартларга рози бўлинг")
        self.assertFalse(User.objects.exists())
        self.assertRedirects(self._reg(agree="on"), reverse("verify_email"))
        u = User.objects.get()
        self.assertTrue(u.terms_accepted_at)
        self.assertEqual(u.terms_version, 1)

    def test_note_mode(self):
        conf.put("terms", {"mode": "note", "body": "<p>Қоида</p>", "version": 3}, None)
        page = self.client.get(reverse("register"))
        self.assertNotContains(page, 'name="agree"')
        self.assertContains(page, "махфийлик сиёсати</a>га рози бўласиз")
        self._reg()
        self.assertEqual(User.objects.get().terms_version, 3)

    def test_pages_render(self):
        self.assertContains(self.client.get(reverse("terms")), "Умумий қоидалар")
        r = self.client.get(reverse("privacy"))
        self.assertContains(r, "Шахсга доир маълумотлар тўғрисида")
        self.assertContains(r, reverse("terms"))
        self.assertContains(self.client.get(reverse("terms")), "Тўлов, тарифлар ва қайтариш")
        self.assertContains(self.client.get(reverse("register")), reverse("privacy"))
        conf.put("about", {"lead": "Биз — жамоа", "team": [{"name": "Али Валиев", "role": "асосчи"}],
                           "body": "<p>Матн<script>x</script></p>"}, None)
        r = self.client.get(reverse("about_us"))
        self.assertContains(r, "Али Валиев")
        self.assertContains(r, "Биз — жамоа")
        self.assertNotContains(r, "<script>x")


class ViewsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.post = Post.objects.create(title="Мақола", slug="maqola", body="Оддий\n\n## Бўлим\n- бир", published=True)

    def test_counts_once_per_visitor_skips_bots_and_staff(self):
        url = reverse("blog_post", args=["maqola"])
        r = self.client.get(url, **UA)
        self.assertContains(r, "<h2>Бўлим</h2>")  # эски оддий матн ҳам ишлайди
        self.client.get(url, **UA)
        self.client.get(url, HTTP_USER_AGENT="Googlebot/2.1")
        self.assertEqual(ViewCount.objects.get(kind="post").n, 1)
        self.client.get(url, HTTP_USER_AGENT="Other browser", REMOTE_ADDR="10.0.0.2")
        self.assertEqual(ViewCount.objects.get(kind="post").n, 2)
        staff = User.objects.create_user("s@x.uz", PW, first_name="S", last_name="S", is_staff=True)
        self.client.force_login(staff)
        self.client.get(url, HTTP_USER_AGENT="Staff", REMOTE_ADDR="10.0.0.3")
        self.assertEqual(ViewCount.objects.get(kind="post").n, 2)
        self.client.logout()
        self.assertContains(self.client.get(reverse("blog"), **UA), "2</span>")


@override_settings(NARXLASH_STAFF_2FA=False)
class PanelEditorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.admin = User.objects.create_superuser("adm@x.uz", PW, first_name="A", last_name="D")
        self.client.force_login(self.admin)

    def test_upload_and_post_html(self):
        with self.settings(MEDIA_ROOT=self.media):
            r = self.client.post(reverse("panel:blog_upload"), {"file": SimpleUploadedFile("a.png", PNG)})
            url = r.json()["url"]
            self.assertTrue(url.startswith("/img/"))
            bad = self.client.post(reverse("panel:blog_upload"), {"file": SimpleUploadedFile("a.png", b"<svg onload=x>")})
            self.assertEqual(bad.status_code, 400)
            got = self.client.get(url)
            self.assertEqual((got.status_code, got["Content-Type"]), (200, "image/png"))
            self.assertEqual(self.client.get("/img/../../etc/passwd").status_code, 404)

            self.client.post(reverse("panel:blog_post_new"), {
                "title": "Расмли", "body": f'<p><b>Қалин</b></p><p><img src="{url}" onerror="x"></p><script>x</script>',
                "published": "1", "cover": "/etc/passwd"})
            p = Post.objects.get()
            self.assertEqual((p.fmt, p.cover), ("html", ""))
            self.assertNotIn("script", p.body)
            self.assertNotIn("onerror", p.body)
            page = self.client.get(reverse("blog_post", args=[p.slug])).content.decode()
            self.assertIn(f'og:image" content="http://testserver{url}"', page)  # улашилганда муқова кўринади
            self.assertIn('og:type" content="article"', page)
            self.assertIn("<strong>Қалин</strong>", page)
            self.assertIn(f'src="{url}"', self.client.get(reverse("blog")).content.decode())  # муқова — биринчи расм
            self.assertEqual(self.client.get(reverse("panel:blog_post_edit", args=[p.pk])).status_code, 200)

    def test_legacy_post_opens_in_editor(self):
        p = Post.objects.create(title="Эски", slug="eski", body="## Бўлим\n- бир")
        r = self.client.get(reverse("panel:blog_post_edit", args=[p.pk]))
        self.assertContains(r, "&lt;h2&gt;Бўлим&lt;/h2&gt;")  # муҳаррирга HTML бўлиб боради

    def test_pages_save(self):
        r = self.client.post(reverse("panel:pages_save"), {
            "tab": "about", "lead": "Лойиҳа", "body": "<p>Биз</p><img src=x onerror=alert(1)>",
            "t_name": ["Али", ""], "t_role": ["асосчи", ""], "t_bio": ["", ""], "t_photo": ["/etc/passwd", ""],
            "f_num": ["100+", ""], "f_label": ["смета", ""], "c_email": "info@x.uz", "c_telegram": "@mhisobot"})
        self.assertEqual(r.status_code, 302)
        a = conf.raw("about")
        self.assertEqual(a["team"], [{"name": "Али", "role": "асосчи", "bio": "", "photo": ""}])
        self.assertEqual((a["body"], a["facts"]), ("<p>Биз</p>", [["100+", "смета"]]))
        self.assertEqual(self.client.get(reverse("panel:pages")).status_code, 200)
        self.client.post(reverse("panel:pages_save"), {"tab": "terms", "mode": "note", "body": "<p>Шарт</p>", "bump": "1"})
        t = conf.raw("terms")
        self.assertEqual((t["mode"], t["version"], t["body"]), ("note", 2, "<p>Шарт</p>"))
        self.assertEqual(self.client.get(reverse("panel:pages") + "?tab=terms").status_code, 200)
        self.assertContains(self.client.get(reverse("about_us")), "t.me/mhisobot")
        self.assertContains(self.client.get(reverse("panel:pages") + "?tab=privacy"), "Шахсга доир маълумотлар")
        self.client.post(reverse("panel:pages_save"), {"tab": "privacy", "body": "<h2>Янги</h2><script>x</script>"})
        self.assertEqual(conf.raw("privacy")["body"], "<h2>Янги</h2>")
        self.assertContains(self.client.get(reverse("privacy")), "<h2>Янги</h2>", html=False)
