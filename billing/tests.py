# i18n: ok-file — тестлар
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing import services as bs
from billing.models import Account, Entry, Payment, Subscription, Tariff, Unlock

User = get_user_model()
ON = override_settings(BILLING_ON=True, BILLING_ROW_PRICE=4, BILLING_OBJECT_PRICE=5000)


class BaseCase(TestCase):
    def setUp(self):
        cache.clear()
        self.u = User.objects.create_user("smeta@mail.uz", "x-pass-123", first_name="Ali", last_name="V",
                                          email_verified=True)
        Tariff.objects.all().delete()
        self.free = Tariff.objects.create(name="Бепул", price=0, rows=2000, objects_limit=1, order=0)
        self.start = Tariff.objects.create(name="Старт", price=100000, rows=30000, objects_limit=5, order=1)

    def bal(self):
        """Жами (ҳақиқий + бонус)."""
        return Account.objects.get(user=self.u).total


class OffTests(BaseCase):
    def test_off_everything_free(self):
        q = bs.quote(self.u, "job:1", Unlock.Kind.ROWS, 99999)
        self.assertTrue(q.free and q.ok and not q.costs)
        u = bs.charge(self.u, "job:1", Unlock.Kind.ROWS, "Смета", 99999)
        self.assertTrue(u.free)
        self.assertFalse(Entry.objects.exists())


@ON
class RowsTests(BaseCase):
    def test_quota_then_balance_then_locked(self):
        u1 = bs.charge(self.u, "job:1", "rows", "A", 1500)
        self.assertEqual((u1.quota_rows, u1.amount), (1500, 0))
        q = bs.quote(self.u, "job:2", "rows", 1000)
        self.assertEqual((q.quota_rows, q.amount, q.ok), (500, 2000, False))
        with self.assertRaises(bs.NotEnough):
            bs.charge(self.u, "job:2", "rows", "B", 1000)
        self.assertFalse(Unlock.objects.filter(key="job:2").exists())  # пул етмади — ҳеч нарса ёзилмади
        bs.post(self.u, 5000, Entry.Kind.TOPUP, "тўлов")
        u2 = bs.charge(self.u, "job:2", "rows", "B", 1000)
        self.assertEqual((u2.quota_rows, u2.amount, self.bal()), (500, 2000, 3000))
        bs.charge(self.u, "job:2", "rows", "B", 1000)  # қайта юклаб олиш — бепул
        self.assertEqual(self.bal(), 3000)
        self.assertTrue(bs.quote(self.u, "job:2", "rows", 1000).paid)
        p = bs.plan(self.u)
        self.assertEqual((p.tariff, p.rows_used, p.rows_left), (self.free, 2000, 0))

    def test_many(self):
        bs.charge(self.u, "job:1", "rows", "A", 800)
        q = bs.quote_many(self.u, [("job:1", 800), ("job:2", 1500), ("job:3", 200)])
        self.assertEqual((q.rows, q.quota_rows, q.amount), (1700, 1200, 2000))
        self.assertTrue(bs.quote_many(self.u, [("job:1", 800)]).paid)

    def test_staff_exempt(self):
        self.u.is_staff = True
        self.u.save()
        self.assertTrue(bs.quote(self.u, "job:9", "rows", 10 ** 6).free)

    def test_objects(self):
        a = bs.charge(self.u, bs.object_key(1), "object", "Мактаб")
        self.assertTrue(a.quota_object and a.amount == 0)
        self.assertEqual(bs.quote(self.u, bs.object_key(2), "object").amount, 5000)
        bs.post(self.u, 6000, Entry.Kind.TOPUP)
        bs.charge(self.u, bs.object_key(2), "object", "Боғча")
        bs.charge(self.u, bs.object_key(2), "object", "Боғча")
        self.assertEqual(self.bal(), 1000)
        self.assertEqual(bs.plan(self.u).objects_used, 1)


@ON
class SubscriptionTests(BaseCase):
    def test_buy_and_renew(self):
        with self.assertRaises(bs.NotEnough):
            bs.buy(self.u, self.start)
        self.assertFalse(Subscription.objects.exists())
        bs.post(self.u, 250000, Entry.Kind.TOPUP)
        sub = bs.buy(self.u, self.start)
        self.assertEqual(self.bal(), 150000)
        p = bs.plan(self.u)
        self.assertEqual((p.tariff, p.rows, p.objects), (self.start, 30000, 5))
        # тугади — ўзи янгиланади (балансда пул бор)
        Subscription.objects.filter(pk=sub.pk).update(start=sub.start - timedelta(days=40), end=timezone.now() - timedelta(days=1))
        p = bs.plan(self.u)
        self.assertEqual(p.tariff, self.start)
        self.assertEqual(Subscription.objects.count(), 2)
        self.assertEqual(self.bal(), 50000)
        # яна тугади — пул етмайди, бепулга тушади
        Subscription.objects.filter(end__gt=timezone.now()).update(end=timezone.now() - timedelta(seconds=1))
        p = bs.plan(self.u)
        self.assertEqual((p.tariff, p.sub), (self.free, None))
        self.assertEqual(self.bal(), 50000)

    def test_tariff_change_keeps_old_terms(self):
        bs.post(self.u, 100000, Entry.Kind.TOPUP)
        bs.buy(self.u, self.start)
        Tariff.objects.filter(pk=self.start.pk).update(rows=10)
        self.assertEqual(bs.plan(self.u).rows, 30000)

    def test_gift_free_and_not_renewed(self):
        sub = bs.buy(self.u, self.start, gift=True)
        self.assertEqual((sub.price, Account.objects.get(user=self.u).balance), (0, 0))
        bs.post(self.u, 500000, Entry.Kind.TOPUP)
        Subscription.objects.filter(pk=sub.pk).update(end=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(bs.plan(self.u).sub)


class LedgerTests(BaseCase):
    def test_ref_once_and_no_negative(self):
        bs.post(self.u, 1000, Entry.Kind.TOPUP, ref="click:77")
        bs.post(self.u, 1000, Entry.Kind.TOPUP, ref="click:77")
        self.assertEqual(self.bal(), 1000)
        with self.assertRaises(bs.NotEnough):
            bs.post(self.u, -1500, Entry.Kind.ADMIN)
        e = bs.post(self.u, -1500, Entry.Kind.ADMIN, allow_negative=True)
        self.assertEqual((e.balance_after, self.bal()), (-500, -500))

    def test_add_month(self):
        d = timezone.now().replace(year=2026, month=1, day=31)
        self.assertEqual((bs.add_month(d).month, bs.add_month(d).day), (2, 28))
        self.assertEqual(bs.add_month(d.replace(month=12, day=5)).year, 2027)


@override_settings(NARXLASH_STAFF_2FA=False)
class PanelTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("admin@mail.uz", "x-pass-123", first_name="A", last_name="B")
        self.client.force_login(self.admin)

    def test_pages_and_actions(self):
        self.assertContains(self.client.get(reverse("panel:billing") + "?tab=tariffs"), "Старт")
        for tab in ("overview", "settings", "ledger", "payments", "unlocks", "log"):
            self.assertEqual(self.client.get(reverse("panel:billing"), {"tab": tab}).status_code, 200)
        r = self.client.get(reverse("panel:billing"), {"tab": "ledger", "kind": "topup", "wallet": "bonus", "q": "x"})
        self.assertEqual(r.status_code, 200)
        r = self.client.get(reverse("panel:user", args=[self.u.pk]) + "?tab=billing")
        self.assertContains(r, "Балансни ўзгартириш")
        self.client.post(reverse("panel:user_balance", args=[self.u.pk]), {"amount": "50 000", "note": "нақд", "bonus": "1"})
        self.assertEqual(self.bal(), 50000)
        self.assertEqual(Entry.objects.get().kind, Entry.Kind.BONUS)
        self.client.post(reverse("panel:user_balance", args=[self.u.pk]), {"amount": "-1000"})  # сабабсиз — йўқ
        self.assertEqual(self.bal(), 50000)
        self.client.post(reverse("panel:user_gift", args=[self.u.pk]), {"tariff": self.start.pk})
        self.assertTrue(Subscription.objects.get().gift)
        self.client.post(reverse("panel:tariff_save", args=[self.start.pk]),
                         {"name": "Старт+", "price": "120000", "rows": "40000", "objects_limit": "6", "order": "1", "active": "1"})
        self.start.refresh_from_db()
        self.assertEqual((self.start.name, self.start.price, self.start.rows), ("Старт+", 120000, 40000))
        self.client.post(reverse("panel:tariff_delete", args=[self.start.pk]))
        self.start.refresh_from_db()
        self.assertFalse(self.start.active)  # обунаси бор — фақат сотувдан олинди
        self.client.post(reverse("panel:tariff_add"), {"name": "Янги", "price": "1", "rows": "1", "objects_limit": "0", "active": "1"})
        self.assertTrue(Tariff.objects.filter(name="Янги").exists())

    def test_settings_tab_save(self):
        url = reverse("panel:billing_settings")
        r = self.client.post(url, {"billing_on": "1", "billing_row_price": "4", "billing_object_price": "5000",
                                   "billing_topup_min": "20000", "billing_topup_max": "1000000",
                                   "billing_disc_3": "5", "billing_disc_6": "15", "billing_disc_12": "30"})
        self.assertRedirects(r, reverse("panel:billing") + "?tab=settings", fetch_redirect_response=False)
        cache.clear()
        self.assertEqual(bs.topup_limits(), (20000, 1000000))
        self.assertEqual(bs.period_discount(12), 30)
        self.assertContains(self.client.get(reverse("panel:billing"), {"tab": "log"}), "Тўлов:")
        # энг кўп < энг кам — сақланмайди
        self.client.post(url, {"billing_topup_min": "50000", "billing_topup_max": "1000", "billing_row_price": "4",
                               "billing_object_price": "5000", "billing_disc_3": "5", "billing_disc_6": "15", "billing_disc_12": "30"})
        cache.clear()
        self.assertEqual(bs.topup_limits(), (20000, 1000000))

    def test_staff_cannot_change(self):
        staff = User.objects.create_user("st@mail.uz", "x-pass-123", first_name="S", last_name="T", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("panel:billing")).status_code, 200)
        r = self.client.post(reverse("panel:user_balance", args=[self.u.pk]), {"amount": "1000", "note": "x"})
        self.assertNotEqual(r.status_code, 302)
        self.assertFalse(Entry.objects.exists())


class ProfileTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.u)
        self.url = reverse("billing:profile")

    @staticmethod
    def msgs(r) -> str:
        return " ".join(str(m) for m in r.context["messages"])

    def test_off_page_says_free_and_no_menu(self):
        r = self.client.get(self.url)
        self.assertContains(r, "Ҳозир ҳамма нарса бепул")
        self.assertNotContains(self.client.get(reverse("dashboard")), self.url)

    @ON
    def test_buy_renew_topup(self):
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, self.url)  # менюда баланс
        self.assertContains(self.client.get(self.url), "учун балансда 100 000 сўм етмайди")  # пул йўқ — тўлдириш тугмаси
        self.client.post(reverse("billing:buy", args=[self.start.pk]))
        self.assertFalse(Subscription.objects.exists())
        bs.post(self.u, 120000, Entry.Kind.TOPUP)
        r = self.client.post(reverse("billing:buy", args=[self.start.pk]), follow=True)
        self.assertIn("тарифи ёқилди", self.msgs(r))
        self.assertEqual(self.bal(), 20000)
        self.assertContains(r, "Тугаганда ўзи янгилансин")
        self.client.post(reverse("billing:renew"), {"auto_renew": "0"})
        self.assertFalse(Account.objects.get(user=self.u).auto_renew)
        r = self.client.post(reverse("billing:topup"), {"amount": "50 000"}, follow=True)
        self.assertIn("Онлайн тўлов ҳали уланмаган", self.msgs(r))
        r = self.client.post(reverse("billing:topup"), {"amount": "5"}, follow=True)
        self.assertIn("сўмгача бўлсин", self.msgs(r))
        self.assertContains(self.client.get(self.url + "?t=ledger"), "Баланс тўлдирилди")

    @ON
    def test_header_shows_plan(self):
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, 'aria-label="Тариф ва баланс"')  # тариф тугмаси ойнаси
        self.assertContains(r, "data-pc-btn")
        self.assertContains(r, "Бепул")
        self.assertContains(self.client.get(reverse("calc")), "data-pc-btn")  # ҳар саҳифада

    def test_header_no_plan_when_off(self):
        self.assertNotContains(self.client.get(reverse("dashboard")), "data-pc-btn")

    @ON
    def test_unverified_email_cannot_pay(self):
        type(self.u).objects.filter(pk=self.u.pk).update(email_verified=False)
        bs.post(self.u, 500000, Entry.Kind.BONUS)
        page = self.client.get(self.url)
        self.assertContains(page, "Аввал email'ни тасдиқланг")
        self.assertNotContains(page, 'id="bp-topup-f"')
        r = self.client.post(reverse("billing:topup"), {"amount": "50 000"}, follow=True)
        self.assertIn("email манзилингизни тасдиқланг", self.msgs(r))
        self.assertFalse(Payment.objects.exists())
        self.client.post(reverse("billing:buy", args=[self.start.pk]))
        self.assertFalse(Subscription.objects.exists())
        self.assertEqual(self.bal(), 500000)

    @ON
    def test_upgrade_message_shows_difference(self):
        bs.post(self.u, 400000, Entry.Kind.TOPUP)
        pro = Tariff.objects.create(name="Про", price=250000, rows=100000, objects_limit=20, order=2)
        self.client.post(reverse("billing:buy", args=[self.start.pk]))
        page = self.client.get(self.url).content.decode()
        self.assertIn("Ўтсангиз:", page)
        self.assertIn("(фақат фарқи)", page)
        r = self.client.post(reverse("billing:buy", args=[pro.pk]), follow=True)
        self.assertIn("ҳисобга олинди, балансдан", self.msgs(r))
        subs = Entry.objects.filter(kind=Entry.Kind.SUBSCRIPTION)
        self.assertEqual(subs.count(), 2)  # бир ечим: эскисини қайтариб, кейин тўлиқ ечиш йўқ
        self.assertFalse(Entry.objects.filter(kind=Entry.Kind.REFUND).exists())

    @ON
    def test_cannot_buy_free_or_hidden(self):
        bs.post(self.u, 10 ** 6, Entry.Kind.TOPUP)
        Tariff.objects.filter(pk=self.start.pk).update(active=False)
        self.client.post(reverse("billing:buy", args=[self.start.pk]))
        self.client.post(reverse("billing:buy", args=[self.free.pk]))
        self.assertFalse(Subscription.objects.exists())
        self.assertEqual(self.bal(), 10 ** 6)


class GateUnitTests(BaseCase):
    """Дарвоза: бепул / тарифдан — дарров; балансдан — тасдиқлаш (402) ва имзоланган ?pay=; етмаса — 402."""

    def setUp(self):
        super().setUp()
        from django.test import RequestFactory
        self.rf = RequestFactory()

    def req(self, qs=""):
        from django.contrib.messages.storage.fallback import FallbackStorage
        r = self.rf.get("/job/x/download/" + qs)
        r.user = self.u
        r.session = self.client.session
        r._messages = FallbackStorage(r)
        return r

    def items(self, rows, key="job:A"):
        return [(key, "rows", "Смета", rows)]

    def test_off_and_quota_pass(self):
        from billing import gate
        self.assertIsNone(gate.check(self.req(), self.items(10 ** 6), "/back/"))
        with ON:
            self.assertIsNone(gate.check(self.req(), self.items(1500, "job:B"), "/back/"))  # тарифдан
            self.assertEqual(Unlock.objects.get(key="job:B").quota_rows, 1500)

    @ON
    def test_confirm_then_pay_and_short(self):
        from billing import gate
        r = gate.check(self.req(), self.items(3000), "/back/")
        self.assertEqual(r.status_code, 402)  # пул йўқ — «етмайди»
        self.assertIn("етмайди".encode(), r.content)
        bs.post(self.u, 10000, Entry.Kind.TOPUP)
        r = gate.check(self.req(), self.items(3000), "/back/")
        self.assertEqual(r.status_code, 402)  # 1000 қатор × 4 = 4000 — тасдиқлаш
        self.assertIn("4 000".encode(), r.content)
        self.assertFalse(Unlock.objects.exists())
        token = gate._token(self.u, self.items(3000), 4000)
        self.assertEqual(gate.check(self.req("?pay=bad"), self.items(3000), "/back/").status_code, 402)
        self.assertIsNone(gate.check(self.req("?pay=" + token), self.items(3000), "/back/"))
        self.assertEqual(self.bal(), 6000)
        self.assertIsNone(gate.check(self.req(), self.items(3000), "/back/"))  # қайта — бепул
        self.assertEqual(self.bal(), 6000)

    @ON
    def test_many_all_or_nothing(self):
        bs.post(self.u, 1000, Entry.Kind.TOPUP)
        items = [("job:1", "rows", "A", 1800), ("job:2", "rows", "B", 1000)]  # 800 қатор × 4 = 3200 > 1000
        with self.assertRaises(bs.NotEnough):
            bs.charge_items(self.u, items)
        self.assertFalse(Unlock.objects.exists())
        self.assertEqual(self.bal(), 1000)

    @ON
    def test_hint_labels(self):
        from billing import gate
        from jobs.models import Job
        job = Job.objects.create(owner=self.u, local_name="a.xlsx", status=Job.Status.DONE, rows_done=2500)
        self.assertEqual(gate.hint(self.u, job)["kind"], "short")
        bs.post(self.u, 5000, Entry.Kind.TOPUP)
        h = gate.hint(self.u, job)
        self.assertEqual((h["kind"], h["amount"], gate.label(h)), ("cost", 2000, "2 000 сўм"))


import texnazor.tests as tnt  # noqa: E402 — синфнинг ўзи импорт қилинмайди (тестлари бу ерда қайта ишламасин)


@override_settings(MEDIA_ROOT=tnt.TMP_MEDIA, BILLING_ON=True, BILLING_OBJECT_PRICE=5000)
class GateTexnazorTests(TestCase):
    """Техназор Ф-2: тарифдаги объект — бепул; ошса — тасдиқлаш, ?pay= билан ечилади; шу ой иккинчиси — бепул."""
    setUp = tnt.TexnazorFlowTests.setUp

    def test_f2_object_month(self):
        from texnazor.models import Period, SiteObject
        cache.clear()
        Tariff.objects.all().delete()
        Tariff.objects.create(name="Бепул", price=0, rows=100, objects_limit=0)
        self.client.post(reverse("texnazor:period_add", args=[self.obj.pk]), {"year": 2026, "month": 10})
        p = Period.objects.get()
        url = reverse("texnazor:f2", args=[self.obj.pk, p.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 402)  # тарифда объект йўқ, балансда пул йўқ
        bs.post(self.user, 5000, Entry.Kind.TOPUP)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 402)
        self.assertIn("5 000".encode(), r.content)
        self.assertIn(b"pay=", r.content)
        r = self.client.get(r.context["pay_url"])
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertEqual(Account.objects.get(user=self.user).balance, 0)
        r = self.client.get(reverse("texnazor:f3", args=[self.obj.pk, p.pk]))  # шу ойда — бепул
        self.assertEqual(r.status_code, 200)
        self.assertEqual(SiteObject.objects.count(), 1)


CLICK = override_settings(CLICK_SERVICE_ID="111", CLICK_MERCHANT_ID="222", CLICK_SECRET_KEY="sekret",
                          CLICK_MERCHANT_USER_ID="")


@CLICK
class ClickTests(BaseCase):
    """Click SHOP API: тўлов саҳифасига йўналтириш, Prepare / Complete, имзо, такрорий ва хато сўровлар."""

    def setUp(self):
        super().setUp()
        self.c = self.client
        self.c.force_login(self.u)

    def start_pay(self, amount=50000):
        from billing.models import Payment
        r = self.c.post(reverse("billing:topup"), {"amount": str(amount)})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("https://my.click.uz/services/pay?"))
        self.assertIn("service_id=111", r["Location"])
        p = Payment.objects.latest("pk")
        self.assertIn(f"transaction_param={p.pk}", r["Location"])
        self.assertEqual((p.amount, p.status), (amount, "created"))
        return p

    def sign(self, d, prep=""):
        import hashlib
        s = f"{d['click_trans_id']}{d['service_id']}sekret{d['merchant_trans_id']}{prep}{d['amount']}{d['action']}{d['sign_time']}"
        return hashlib.md5(s.encode()).hexdigest()

    def req(self, p, action, trans="9001", amount=None, prep=None, error="0", bad_sign=False):
        d = {"click_trans_id": trans, "service_id": "111", "click_paydoc_id": "77", "merchant_trans_id": str(p.pk),
             "amount": amount if amount is not None else f"{p.amount}.00", "action": str(action), "error": error,
             "error_note": "Success", "sign_time": "2026-11-24 12:00:00"}
        if action == 1:
            d["merchant_prepare_id"] = str(prep if prep is not None else p.pk)
        d["sign_string"] = "0" * 32 if bad_sign else self.sign(d, d.get("merchant_prepare_id", ""))
        url = reverse("billing:click_prepare" if action == 0 else "billing:click_complete")
        from django.test import Client
        return Client().post(url, d).json()  # Click — кирмаган, CSRF'сиз

    def test_full_flow_and_idempotent(self):
        p = self.start_pay(50000)
        r = self.req(p, 0)
        self.assertEqual(r["error"], 0, r)
        self.assertEqual(r["merchant_prepare_id"], p.pk)
        r = self.req(p, 1)
        self.assertEqual(r["error"], 0, r)
        p.refresh_from_db()
        self.assertEqual(p.status, "paid")
        self.assertEqual(self.bal(), 50000)
        e = Entry.objects.get(ref=f"click:{p.pk}")
        self.assertEqual((e.kind, e.amount, r["merchant_confirm_id"]), ("topup", 50000, e.pk))
        # Click қайта юборса — пул иккинчи марта тушмайди
        self.assertEqual(self.req(p, 1)["error"], -4)
        self.assertEqual(self.req(p, 0)["error"], -4)
        self.assertEqual(self.bal(), 50000)

    def test_bad_sign_amount_action_order(self):
        p = self.start_pay(50000)
        self.assertEqual(self.req(p, 0, bad_sign=True)["error"], -1)
        self.assertEqual(self.req(p, 0, amount="49000")["error"], -2)
        p2 = type(p).objects.create(user=self.u, amount=1000)
        p2.pk_saved = p2.pk
        p2.delete()
        p2.pk = p2.pk_saved
        self.assertEqual(self.req(p2, 0)["error"], -5)
        # Prepare'сиз Complete — транзакция йўқ
        self.assertEqual(self.req(p, 1)["error"], -6)
        self.assertEqual(self.bal() if Account.objects.filter(user=self.u).exists() else 0, 0)

    def test_click_error_cancels(self):
        p = self.start_pay(10000)
        self.assertEqual(self.req(p, 0)["error"], 0)
        self.assertEqual(self.req(p, 1, error="-5017")["error"], -9)
        p.refresh_from_db()
        self.assertEqual(p.status, "cancelled")
        self.assertEqual(self.req(p, 0, trans="9002")["error"], -9)
        self.assertFalse(Entry.objects.exists())

    def test_wrong_prepare_id(self):
        p = self.start_pay(10000)
        self.req(p, 0)
        self.assertEqual(self.req(p, 1, prep=p.pk + 1000)["error"], -6)
        self.assertFalse(Entry.objects.exists())

    def test_return_page_message(self):
        p = self.start_pay(10000)
        self.req(p, 0)
        self.req(p, 1)
        r = self.c.get(reverse("billing:profile") + f"?paid={p.pk}", follow=True)
        msgs = [str(m) for m in r.context["messages"]]
        self.assertTrue(any("10 000" in m for m in msgs), msgs)
        # бошқа одамнинг тўлови — ҳеч нарса айтилмайди
        other = User.objects.create_user("b@mail.uz", "x-pass-123", email_verified=True)
        from django.test import Client
        c2 = Client()
        c2.force_login(other)
        r = c2.get(reverse("billing:profile") + f"?paid={p.pk}", follow=True)
        self.assertFalse(list(r.context["messages"]))

    @override_settings(CLICK_SECRET_KEY="")
    def test_not_configured(self):
        r = self.c.post(reverse("billing:topup"), {"amount": "50000"})
        self.assertRedirects(r, reverse("billing:profile"), fetch_redirect_response=False)
        from billing.models import Payment
        self.assertFalse(Payment.objects.exists())


@CLICK
class BillingCheckTests(BaseCase):
    def run_cmd(self, *args):
        import io
        from django.core.management import call_command
        out = io.StringIO()
        code = 0
        try:
            call_command("billing_check", *args, stdout=out)
        except SystemExit as e:
            code = e.code
        return code, out.getvalue()

    def test_ok_and_ledger_mismatch(self):
        bs.post(self.u, 5000, Entry.Kind.TOPUP, "x")
        code, out = self.run_cmd("--site", "https://mhisobot.uz")
        self.assertEqual(code, 0, out)
        self.assertIn("https://mhisobot.uz/balance/click/prepare/", out)
        Account.objects.filter(user=self.u).update(balance=9999)
        code, out = self.run_cmd("--site", "https://mhisobot.uz")
        self.assertEqual(code, 1)
        self.assertIn("ёзувлар йиғиндиси 5000 / 0", out)

    def test_ping_signs_and_hits_prepare(self):
        """--ping: ўз Prepare манзилига имзоланган сўров → -5 (буюртма йўқ), пул ҳаракатланмайди."""
        import io
        import json
        from unittest import mock
        from urllib.parse import parse_qs, urlsplit
        from django.test import Client

        def fake_urlopen(req, timeout=0):
            data = {k: v[0] for k, v in parse_qs(req.data.decode()).items()}
            r = Client().post(urlsplit(req.full_url).path, data)
            return mock.MagicMock(__enter__=lambda s: io.BytesIO(json.dumps(r.json()).encode()), __exit__=lambda *a: False)

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            code, out = self.run_cmd("--site", "https://mhisobot.uz", "--ping")
        self.assertEqual(code, 0, out)
        self.assertIn("имзо ўтди", out)

        def tampered(req, timeout=0):  # сайтдаги калит бошқа бўлгандек — имзо мос келмайди
            req.data = req.data.replace(b"sign_string=", b"sign_string=0")
            return fake_urlopen(req, timeout)

        with mock.patch("urllib.request.urlopen", tampered):
            code, out = self.run_cmd("--site", "https://mhisobot.uz", "--ping")
        self.assertEqual(code, 1)
        self.assertIn("имзо ўтмади", out)


@ON
class TariffChangeTests(BaseCase):
    """Тариф ёқиқ турганда бошқасини танлаш: қимматроғи — дарров (ишлатилмагани ҳисобга), арзонроғи — кейинги даврдан."""

    def setUp(self):
        super().setUp()
        self.pro = Tariff.objects.create(name="Про", price=250000, rows=100000, objects_limit=20, order=2)

    def test_upgrade_credits_unused_part(self):
        bs.post(self.u, 400000, Entry.Kind.TOPUP)
        bs.change(self.u, self.start)                       # −100 000
        bs.charge(self.u, "job:1", Unlock.Kind.ROWS, "a", 15000)  # тарифдаги 30 000 қаторнинг ярми
        o = bs.offer(self.u, self.pro)
        self.assertEqual(o.kind, "upgrade")
        self.assertAlmostEqual(o.credit, 50000, delta=100)  # кунлар ~100%, қаторлар 50% — кичиги
        how, sub = bs.change(self.u, self.pro)
        self.assertEqual(how, "now")
        self.assertEqual(self.bal(), 300000 - o.pay)
        self.assertEqual(sub.price, 250000)  # обуна қиймати — тўлиқ нарх (кейинги ўтишда шундан ҳисобланади)
        self.assertEqual(Entry.objects.filter(kind="subscription").first().amount, -o.pay)
        p = bs.plan(self.u)
        self.assertEqual((p.tariff, p.rows_used), (self.pro, 0))  # янги давр — қаторлар янгидан
        self.assertIn("ҳисобга олинди", Entry.objects.filter(kind="subscription").first().note)

    def test_upgrade_late_in_period_credits_less(self):
        bs.post(self.u, 400000, Entry.Kind.TOPUP)
        sub = bs.buy(self.u, self.start)
        Subscription.objects.filter(pk=sub.pk).update(start=sub.start - timedelta(days=24), end=sub.end - timedelta(days=24))
        c = bs.credit(self.u)
        self.assertLess(c, 30000)
        self.assertGreater(c, 10000)

    def test_downgrade_waits_for_period_end(self):
        bs.post(self.u, 400000, Entry.Kind.TOPUP)
        bs.change(self.u, self.pro)                         # 150 000 қолди
        self.assertEqual(bs.offer(self.u, self.start).kind, "next")
        how, sub = bs.change(self.u, self.start)
        self.assertEqual((how, sub), ("next", None))
        self.assertEqual(self.bal(), 150000)                # пул ҳозир ечилмайди
        self.assertEqual(bs.plan(self.u).tariff, self.pro)  # ҳозиргиси давом этади
        self.assertEqual(bs.offer(self.u, self.start).kind, "scheduled")
        Account.objects.filter(user=self.u).update(auto_renew=False)  # ўзи янгиланиш ўчиқ бўлса ҳам — танлагани олинади
        Subscription.objects.update(start=timezone.now() - timedelta(days=31), end=timezone.now() - timedelta(seconds=1))
        p = bs.plan(self.u)
        self.assertEqual(p.tariff, self.start)
        self.assertEqual(self.bal(), 50000)
        self.assertIsNone(Account.objects.get(user=self.u).next_tariff)

    def test_cancel_next_and_same_tariff(self):
        bs.post(self.u, 500000, Entry.Kind.TOPUP)
        bs.change(self.u, self.pro)
        with self.assertRaises(bs.BillingError):
            bs.change(self.u, self.pro)
        bs.change(self.u, self.start)
        bs.cancel_next(self.u)
        Account.objects.filter(user=self.u).update(auto_renew=True)
        Subscription.objects.update(start=timezone.now() - timedelta(days=31), end=timezone.now() - timedelta(seconds=1))
        self.assertEqual(bs.plan(self.u).tariff, self.pro)  # бекор қилинди — ўша тариф янгиланди

    @override_settings(BILLING_TOPUP_MIN=10000, BILLING_TOPUP_MAX=5000000)
    def test_topup_limits_checked_on_server(self):
        from billing.models import Payment
        self.client.force_login(self.u)
        for bad in ("9 999", "5 000 001", "12.5", "-50000", "1e6", ""):
            r = self.client.post(reverse("billing:topup"), {"amount": bad}, follow=True)
            self.assertIn("10 000 дан 5 000 000 сўмгача", " ".join(str(m) for m in r.context["messages"]), bad)
        with CLICK:
            r = self.client.post(reverse("billing:topup"), {"amount": "5 000 000"})
            self.assertTrue(r["Location"].startswith("https://my.click.uz/"))
        self.assertEqual(list(Payment.objects.values_list("amount", flat=True)), [5000000])
        page = self.client.get(reverse("billing:profile"))
        self.assertContains(page, "MIN = 10000, MAX = 5000000")


@ON
class WalletTests(BaseCase):
    """Ҳақиқий пул ва бонус аралашмайди: сарфлашда аввал бонус, админ ҳар бирини алоҳида тузатади."""

    def acc(self):
        return Account.objects.get(user=self.u)

    def test_bonus_spent_first_and_separate(self):
        bs.post(self.u, 30000, Entry.Kind.TOPUP, "Click")
        bs.post(self.u, 20000, Entry.Kind.BONUS, "совға")
        a = self.acc()
        self.assertEqual((a.balance, a.bonus, a.total), (30000, 20000, 50000))
        e = bs.post(self.u, -25000, Entry.Kind.ROWS, "смета")
        self.assertEqual((e.bonus, e.real), (-20000, -5000))
        a = self.acc()
        self.assertEqual((a.balance, a.bonus), (25000, 0))
        self.assertEqual((e.balance_after, e.bonus_after, e.total_after), (25000, 0, 25000))
        with self.assertRaises(bs.NotEnough):
            bs.post(self.u, -25001, Entry.Kind.ROWS)

    def test_admin_real_minus_does_not_eat_bonus(self):
        bs.post(self.u, 10000, Entry.Kind.TOPUP)
        bs.post(self.u, 50000, Entry.Kind.BONUS)
        with self.assertRaises(bs.NotEnough):
            bs.post(self.u, -20000, Entry.Kind.ADMIN, "x", bonus=0)
        bs.post(self.u, -10000, Entry.Kind.ADMIN, "қайтарилди", bonus=0)
        self.assertEqual((self.acc().balance, self.acc().bonus), (0, 50000))

    def test_subscription_records_bonus_share(self):
        bs.post(self.u, 40000, Entry.Kind.TOPUP)
        bs.post(self.u, 60000, Entry.Kind.BONUS)
        sub = bs.buy(self.u, self.start)
        self.assertEqual((sub.price, sub.paid_bonus), (100000, 60000))
        self.assertEqual((self.acc().balance, self.acc().bonus), (0, 0))


@ON
class PeriodTests(BaseCase):
    """3 / 6 / 12 ойлик обуна ва тариф чегирмаси."""

    def test_price_for_periods_and_tariff_discount(self):
        with override_settings(BILLING_DISC_3=10, BILLING_DISC_6=15, BILLING_DISC_12=25):
            cache.clear()
            self.assertEqual(bs.price_for(self.start, 1).total, 100000)
            self.assertEqual(bs.price_for(self.start, 3).total, 270000)
            self.assertEqual(bs.price_for(self.start, 12).total, 900000)
            Tariff.objects.filter(pk=self.start.pk).update(discount=20)
            self.start.refresh_from_db()
            p = bs.price_for(self.start, 12)
            self.assertEqual((p.total, p.pct, p.per_month), (720000, 40, 60000))
            Tariff.objects.filter(pk=self.start.pk).update(discount_until=timezone.localdate() - timedelta(days=1))
            self.start.refresh_from_db()
            self.assertEqual(bs.price_for(self.start, 1).total, 100000)  # акция тугади

    def test_three_months_rows_reset_monthly(self):
        bs.post(self.u, 10 ** 6, Entry.Kind.TOPUP)
        sub = bs.buy(self.u, self.start, months=3)
        self.assertEqual(sub.months, 3)
        self.assertEqual(sub.end, bs.add_month(sub.start, 3))
        bs.charge(self.u, "job:1", Unlock.Kind.ROWS, "a", 30000)
        self.assertEqual(bs.plan(self.u).rows_left, 0)
        later = bs.add_month(sub.start, 1) + timedelta(hours=1)  # иккинчи ой — қаторлар янгидан
        p = bs.plan(self.u, now=later)
        self.assertEqual((p.rows_left, p.sub.pk), (30000, sub.pk))
        # иккинчи ойда кредит: қолган 1 тўлиқ ой + шу ойнинг деярли ҳаммаси
        c = bs.credit(self.u, now=later)
        self.assertGreater(c, sub.price // 3 * 1.9)
        self.assertLessEqual(c, sub.price * 2 // 3)

    def test_same_tariff_other_period_is_next(self):
        bs.post(self.u, 10 ** 6, Entry.Kind.TOPUP)
        bs.change(self.u, self.start, 1)
        o = bs.offer(self.u, self.start, 12)
        self.assertEqual(o.kind, "next")
        bs.change(self.u, self.start, 12)
        a = Account.objects.get(user=self.u)
        self.assertEqual((a.next_tariff_id, a.next_months), (self.start.pk, 12))
        Subscription.objects.update(start=timezone.now() - timedelta(days=31), end=timezone.now() - timedelta(seconds=1))
        p = bs.plan(self.u)
        self.assertEqual((p.sub.months, p.tariff), (12, self.start))

    def test_upgrade_from_long_cheap_returns_extra(self):
        pro = Tariff.objects.create(name="Про", price=250000, rows=100000, objects_limit=20, order=2)
        bs.post(self.u, 2 * 10 ** 6, Entry.Kind.TOPUP)
        old = bs.buy(self.u, self.start, months=12)   # ~900 000 тўланди
        before = self.bal()
        o = bs.offer(self.u, pro, 1)
        self.assertEqual((o.kind, o.pay), ("upgrade", 0))
        self.assertGreater(o.back, 0)
        bs.change(self.u, pro, 1)
        self.assertEqual(self.bal(), before + o.back)  # ортгани балансга қайтди
        self.assertTrue(Entry.objects.filter(kind="refund", ref__endswith=":back").exists())
        self.assertLess(old.price, 10 ** 6)
