"""``manage.py billing_check [--site https://mhisobot.uz] [--ping]`` — пулли режимни ёқишдан олдинги текширув.

* тарифлар: бепул тариф борми, фаол тарифлар;
* Click: .env да калитлар, Click кабинетига бериладиган манзиллар;
* ``--ping`` — сайтнинг ўз Prepare манзилига мавжуд бўлмаган буюртма билан ИМЗОЛАНГАН сўров юборади. Жавоб
  «-5 (буюртма йўқ)» бўлса — манзил ташқаридан очилади, калит тўғри ва имзо ўтди. Пул ҳаракатланмайди;
* дафтар: ҳар бир ҳисобда ҳақиқий пул ва бонус ёзувлар йиғиндисига тенгми;
* осилиб қолган тўловлар (Prepare бўлган, Complete келмаган — 1 соатдан ортиқ).
Муаммо бўлса — чиқиш коди 1."""
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from billing import click
from billing import services as bs
from billing.models import Account, Entry, Payment, Tariff


class Command(BaseCommand):
    help = "Тўлов тизими: тарифлар, Click созламаси, дафтар тўғрилиги"

    def add_arguments(self, parser):
        parser.add_argument("--site", default="", help="сайт манзили (бўлмаса — ALLOWED_HOSTS'даги биринчи домен)")
        parser.add_argument("--ping", action="store_true", help="Prepare манзилига имзоланган синов сўрови (пулсиз)")

    def ok(self, msg):
        self.stdout.write(f"  OK    {msg}")

    def warn(self, msg):
        self.warns += 1
        self.stdout.write(f"  !     {msg}")

    def bad(self, msg):
        self.errors += 1
        self.stdout.write(f"  XATO  {msg}")

    def site(self, opt):
        if opt:
            return opt.rstrip("/")
        hosts = [h.lstrip(".") for h in settings.ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1", "*")]
        return f"https://{hosts[0]}" if hosts else "http://127.0.0.1:8000"

    def handle(self, site, ping, **opts):
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        self.errors = self.warns = 0
        base = self.site(site)

        self.stdout.write("Режим")
        self.stdout.write(f"  пулли режим: {'ЁҚИҚ' if bs.enabled() else 'ўчиқ (ҳамма нарса бепул)'} · "
                          f"ортиқча қатор {bs.row_price()} сўм · объект {bs.object_price()} сўм / ой")

        self.stdout.write("Тарифлар")
        active = list(Tariff.objects.filter(active=True))
        free = [t for t in active if t.free]
        if free:
            t = free[0]
            self.ok(f"бепул тариф «{t.name}»: {t.rows} қатор, {t.objects_limit} объект")
        else:
            self.bad("фаол бепул тариф (нархи 0) йўқ — тарифсиз фойдаланувчи ҳеч нарса юклай олмайди")
        for t in active:
            if not t.free:
                self.ok(f"«{t.name}»: {t.price} сўм / ой · {t.rows} қатор · {t.objects_limit} объект")

        self.stdout.write("Click")
        prep = base + reverse("billing:click_prepare")
        comp = base + reverse("billing:click_complete")
        if click.ready():
            self.ok(f"калитлар .env да бор · service_id {click.conf('SERVICE_ID')} · merchant_id {click.conf('MERCHANT_ID')}")
        else:
            miss = [k for k in ("SERVICE_ID", "MERCHANT_ID", "SECRET_KEY") if not click.conf(k)]
            (self.bad if bs.enabled() else self.warn)(
                "калит йўқ: " + ", ".join("CLICK_" + k for k in miss) + " — «Балансни тўлдириш» ишламайди")
        self.stdout.write(f"  Click кабинетига:  Prepare URL  {prep}")
        self.stdout.write(f"                     Complete URL {comp}")
        if not base.startswith("https://"):
            self.warn("манзил https эмас — Click фақат https манзилга мурожаат қилади")
        if ping:
            self.ping(prep)

        self.stdout.write("Дафтар")
        rows = Entry.objects.values("user").annotate(s=Sum("amount"), b=Sum("bonus"))
        sums = {r["user"]: ((r["s"] or 0) - (r["b"] or 0), r["b"] or 0) for r in rows}
        wrong = [(a.user_id, a.balance, a.bonus, *sums.get(a.user_id, (0, 0))) for a in Account.objects.all()
                 if (a.balance, a.bonus) != sums.get(a.user_id, (0, 0))]
        if wrong:
            for uid, bal, bon, s_real, s_bon in wrong[:20]:
                self.bad(f"фойдаланувчи #{uid}: ҳақиқий {bal} / бонус {bon}, ёзувлар йиғиндиси {s_real} / {s_bon}")
        else:
            self.ok(f"{Account.objects.count()} та ҳисоб — иккала ҳамён ёзувлар йиғиндисига тенг")
        neg_b = Account.objects.filter(bonus__lt=0).count()
        if neg_b:
            self.bad(f"{neg_b} та ҳисобда бонус манфий")
        neg = Account.objects.filter(balance__lt=0).count()
        if neg:
            self.warn(f"{neg} та ҳисобда манфий баланс (админ тузатиши)")

        stuck = Payment.objects.filter(status=Payment.Status.PREPARED, created__lt=timezone.now() - timedelta(hours=1))
        if stuck.exists():
            self.warn(f"{stuck.count()} та тўлов Prepare'да қолган (Complete келмаган) — Click кабинетида текширинг: "
                      + ", ".join(f"#{p.pk}" for p in stuck[:10]))
        paid = Payment.objects.filter(status=Payment.Status.PAID)
        no_entry = [p.pk for p in paid if not Entry.objects.filter(ref=f"click:{p.pk}").exists()]
        if no_entry:
            self.bad("тўланган, лекин балансга ёзилмаган тўловлар: " + ", ".join(f"#{x}" for x in no_entry[:20]))
        else:
            self.ok(f"{paid.count()} та тўланган тўлов — ҳаммаси балансга ёзилган")

        self.stdout.write("")
        if self.errors:
            self.stdout.write(f"{self.errors} та хато, {self.warns} та огоҳлантириш")
            sys.exit(1)
        self.stdout.write(f"Хато йўқ ({self.warns} та огоҳлантириш)" if self.warns else "Ҳаммаси жойида")

    def ping(self, url):
        if not click.ready():
            self.bad("--ping: калитларсиз синаб бўлмайди")
            return
        d = {"click_trans_id": "0", "service_id": click.conf("SERVICE_ID"), "click_paydoc_id": "0",
             "merchant_trans_id": "0", "amount": "1000", "action": "0", "error": "0", "error_note": "ping",
             "sign_time": time.strftime("%Y-%m-%d %H:%M:%S")}
        d["sign_string"] = click._sign(d)
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(d).encode(), method="POST",
                                         headers={"User-Agent": "billing_check"})
            with urllib.request.urlopen(req, timeout=15) as r:
                res = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            self.bad(f"--ping: {url} → HTTP {e.code} (nginx / сайт сўровни ўтказмади)")
            return
        except Exception as e:  # noqa: BLE001
            self.bad(f"--ping: {url} очилмади: {e}")
            return
        code = res.get("error")
        if code == click.NOT_FOUND:
            self.ok("--ping: манзил очилади, имзо ўтди (жавоб -5 «буюртма йўқ» — шундай бўлиши керак)")
        elif code == click.SIGN_FAILED:
            self.bad("--ping: имзо ўтмади — сайтдаги CLICK_SECRET_KEY бошқа (хизмат қайта ишга туширилганми?)")
        else:
            self.bad(f"--ping: кутилмаган жавоб {res}")
