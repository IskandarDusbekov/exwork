"""Эълонлар: чоп этилгани кирганда бир марта ойнада, кейин қўнғироқчада; эскиси янги одамга чиқмайди."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Announcement, AnnouncementRead
from .services import for_user


class NewsTests(TestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_user(email="n@example.com", password="x-test-123")
        self.u.date_joined = timezone.now() - timedelta(days=10)
        self.u.save()

    def _ann(self, **kw):
        a = Announcement(title=kw.pop("title", "Янги имконият"), body="Биринчи\n\n- бир\n- икки", **kw)
        a.publish()
        a.save()
        return a

    def test_blocks(self):
        a = Announcement(body="Салом\nдунё\n\n- бир\n- икки\n\n\nОхири")
        self.assertEqual(a.blocks, [("p", "Салом\nдунё"), ("ul", ["бир", "икки"]), ("p", "Охири")])

    def test_modal_once_then_bell(self):
        a = self._ann()
        d = for_user(self.u)
        self.assertEqual((d["unread"], d["modal"]), (1, a))
        self.client.force_login(self.u)
        r = self.client.post(reverse("news:read"), {"ids": [a.pk]})
        self.assertEqual(r.json()["n"], 1)
        d = for_user(self.u)
        self.assertEqual((d["unread"], d["modal"], len(d["items"])), (0, None, 1))  # қўнғироқчада қолди

    def test_draft_expired_old_hidden(self):
        Announcement.objects.create(title="қоралама")
        self._ann(title="тугаган", ends_at=timezone.now() - timedelta(hours=1))
        old = self._ann(title="эски")
        Announcement.objects.filter(pk=old.pk).update(published_at=timezone.now() - timedelta(days=30))
        quiet = self._ann(title="жим", show_modal=False)
        d = for_user(self.u)
        self.assertEqual([a.title for a in d["items"]], ["жим", "эски"])
        self.assertEqual(d["unread"], 1)  # эскиси — рўйхатдан ўтишдан олдин: ўқилган ҳисобланади
        self.assertIsNone(d["modal"])     # «жим» — фақат қўнғироқчада
        self.assertTrue(next(a for a in d["items"] if a.pk == quiet.pk).unread)

    def test_page_renders_bell_and_modal(self):
        a = self._ann(style="celebrate", title="Байрам муборак")
        self.client.force_login(self.u)
        h = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("data-nw-bell", h)
        self.assertIn(f'id="nw-tpl-{a.pk}"', h)
        self.assertIn(f"open({a.pk})", h)  # ўқилмаган — ойна ўзи очилади
        AnnouncementRead.objects.create(announcement=a, user=self.u)
        h = self.client.get(reverse("dashboard")).content.decode()
        self.assertNotIn(f"open({a.pk})", h)

    def test_read_ignores_unpublished(self):
        a = Announcement.objects.create(title="қоралама")
        self.client.force_login(self.u)
        self.assertEqual(self.client.post(reverse("news:read"), {"ids": [a.pk]}).json()["n"], 0)


@override_settings(NARXLASH_STAFF_2FA=False)
class PanelNewsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(email="adm@example.com", password="x-test-123")
        self.client.force_login(self.admin)

    def test_create_publish_unpublish(self):
        r = self.client.post(reverse("panel:news_save"), {"title": "Табрик", "body": "Матн", "style": "celebrate",
                                                          "show_modal": "1", "action": "publish", "link_url": "javascript:x"})
        a = Announcement.objects.get()
        self.assertRedirects(r, reverse("panel:news") + f"?edit={a.pk}", fetch_redirect_response=False)
        self.assertTrue(a.published and a.published_at and a.show_modal)
        self.assertEqual((a.style, a.link_url), ("celebrate", ""))  # хавфли ҳавола олинмайди
        self.assertEqual(self.client.get(reverse("panel:news") + f"?edit={a.pk}").status_code, 200)
        self.client.post(reverse("panel:news_save"), {"id": a.pk, "title": "Табрик", "style": "news", "action": "unpublish"})
        a.refresh_from_db()
        self.assertFalse(a.published)
        self.client.post(reverse("panel:news_delete", args=[a.pk]))
        self.assertFalse(Announcement.objects.exists())
