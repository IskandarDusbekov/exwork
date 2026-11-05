"""Панел → «Ресурс базаси» → «Юклаш»: RES файлни тўғридан-тўғри базага юклаш, такрор ва алмаштириш."""
import io
import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from accounts.models import User

from . import services
from .models import BaseConfig, Observation, Source
from .tests_resfile import RS_ROWS, rs_sheet

PASSWORD = "Kuchli-parol-2026"
TMP_MEDIA = Path(tempfile.mkdtemp(prefix="narxlash_resupload_"))
PERIOD = {"region": "toshkent_sh", "year": "2025", "quarter": "2"}


def xlsx(rows=RS_ROWS) -> bytes:
    wb = Workbook()
    rs_sheet(wb.active, rows)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(name, data=None):
    return SimpleUploadedFile(name, data if data is not None else xlsx(),
                              content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class UploadTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        shutil.rmtree(TMP_MEDIA / services.UPLOAD_DIR, ignore_errors=True)  # oldingi testning nusxalari
        self.admin =User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        self.client.force_login(self.admin)
        self.url = reverse("panel:base_upload")

    def post(self, *files, **period):
        return self.client.post(self.url, {**PERIOD, **period, "files": list(files)}, follow=True)

    def files(self) -> list:
        d = TMP_MEDIA / services.UPLOAD_DIR
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def test_access(self):
        staff = User.objects.create_user("staff@test.uz", PASSWORD, first_name="S", last_name="T", is_staff=True)
        user = User.objects.create_user("user@test.uz", PASSWORD, first_name="U", last_name="V")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("panel:base_data")).status_code, 404)
        self.client.force_login(staff)
        self.assertContains(self.client.get(reverse("panel:base_data")), "Файл юклаш (RES ёки ЛРВ)", status_code=200, count=0)  # шакл кўринмайди
        self.assertEqual(self.client.post(self.url, {**PERIOD, "files": [upload("a.xlsx")]}).status_code, 403)
        self.assertFalse(Source.objects.exists())

    def test_period_required(self):
        page = self.post(upload("a.xlsx"), year="")
        self.assertContains(page, "Аввал вилоят ва йилни танланг")
        self.assertFalse(Source.objects.exists())
        self.assertEqual(self.files(), [])

    def test_upload_adds_coded_prices(self):
        page = self.post(upload("bp1010.xlsx"))
        self.assertContains(page, "қўшилди")
        s = Source.objects.get()
        self.assertEqual((s.kind, s.title, s.region, s.year, s.quarter), ("file", "bp1010.xlsx", "toshkent_sh", 2025, None))  # чорак ёзилмайди
        self.assertEqual(sorted(Observation.objects.values_list("resource__code", flat=True)), ["1", "258", "9219"])
        self.assertEqual(s.stats["skipped"]["no_code"], 2)  # светильник, насос — ҳозирча олинмайди
        self.assertTrue(services._media(s.file_path).exists())
        cfg = BaseConfig.get()
        cfg.enabled, cfg.region, cfg.year, cfg.quarter = True, "toshkent_sh", 2025, 2
        cfg.save()
        self.assertEqual(services.price_map(cfg.period)["258"]["price"], 207913.0)

    def test_same_file_same_period_skip(self):
        data = xlsx()  # bir xil baytlar (openpyxl har saqlashda vaqtni yozadi — qayta yasalsa xeshi boshqa)
        self.post(upload("a.xlsx", data))
        page = self.post(upload("a.xlsx", data))
        self.assertContains(page, "Такрор файллар — қарор кутмоқда (1)")
        self.assertContains(page, "айнан шу файл")
        self.assertEqual(Source.objects.count(), 1)
        self.assertEqual(len(self.files()), 2)  # қарор кутаётган нусха
        token = next(iter(self.client.session["resbase_upload_pending"]))
        self.client.post(reverse("panel:base_upload_pending", args=[token, "skip"]))
        self.assertEqual(Source.objects.count(), 1)
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(self.client.session["resbase_upload_pending"], {})

    def test_same_content_other_name_replace(self):
        self.post(upload("a.xlsx"))
        old = Source.objects.get()
        changed_bytes = xlsx(list(reversed(RS_ROWS[4:])) + RS_ROWS[:4])  # бошқа файл (байтлари), нархлар айнан бир хил
        page = self.post(upload("b.xlsx", changed_bytes))
        self.assertContains(page, "мазмуни айнан бир хил файл")
        token = next(iter(self.client.session["resbase_upload_pending"]))
        page = self.client.post(reverse("panel:base_upload_pending", args=[token, "replace"]), follow=True)
        self.assertContains(page, "«b.xlsx» алмаштирилди: 3 та нарх")
        new = Source.objects.get()
        self.assertNotEqual(new.pk, old.pk)
        self.assertEqual(new.title, "b.xlsx")
        self.assertEqual(Observation.objects.count(), 3)
        self.assertEqual(self.files(), [Path(new.file_path).name])  # эскисининг нусхаси ўчди

    def test_new_version_is_not_duplicate(self):
        """Шу даврга нархлари бошқа файл — янги кузатувлар (медианага қўшилади), такрор эмас."""
        self.post(upload("a.xlsx"))
        changed = [r[:] for r in RS_ROWS]
        changed[5][5] = 210000
        self.post(upload("a2.xlsx", xlsx(changed)))
        self.assertEqual(Source.objects.count(), 2)
        self.assertNotIn("resbase_upload_pending", {k for k, v in self.client.session.items() if v})

    def test_same_file_other_period_warns(self):
        self.post(upload("a.xlsx"))
        page = self.post(upload("a.xlsx"), year="2026")  # давр — вилоят ва йил
        self.assertContains(page, "бу файл бошқа даврга ҳам юкланган")
        self.assertEqual(sorted(Source.objects.values_list("year", flat=True)), [2025, 2026])

    def test_not_res_file(self):
        """Na narx jadvali, na norma shifrli ish (masalan, svodka) — saqlanmaydi."""
        wb = Workbook()
        ws = wb.active
        ws.append(["N п.п.", "Шифр", "Наименование работ и затрат", "Единица измерения", "Количество"])
        ws.append([1, "ГЛ.1", "ПОДГОТОВКА ТЕРРИТОРИИ", "СУМ", 1.5])
        buf = io.BytesIO()
        wb.save(buf)
        page = self.post(upload("svodka.xlsx", buf.getvalue()), upload("notes.txt", b"salom"))
        self.assertContains(page, "кодли нархли ресурс ҳам, норма шифрли иш ҳам топилмади")
        self.assertContains(page, "фақат .xls, .xlsx, .xlsm")
        self.assertFalse(Source.objects.exists())
        self.assertEqual(self.files(), [])

    def test_reharvest_and_delete_file_source(self):
        self.post(upload("a.xlsx"))
        s = Source.objects.get()
        Observation.objects.all().delete()
        self.client.post(reverse("panel:base_source", args=[s.pk, "reharvest"]))
        self.assertEqual(Observation.objects.count(), 3)
        page = self.client.get(reverse("panel:base_sources"))
        self.assertContains(page, "a.xlsx")
        self.client.post(reverse("panel:base_source", args=[s.pk, "delete"]))
        self.assertFalse(Source.objects.exists())
        self.assertEqual(self.files(), [])
        self.assertEqual(self.client.post(reverse("panel:base_add", args=["file", s.work_id])).status_code, 404)


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class DataPageTests(TestCase):
    """«Маълумотлар»: дарахт, ресурс тури бўйича ажратиш, фильтр, қидирув, саҳифалаш."""

    def setUp(self):
        shutil.rmtree(TMP_MEDIA / services.UPLOAD_DIR, ignore_errors=True)
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        self.client.force_login(self.admin)
        up = reverse("panel:base_upload")
        self.client.post(up, {**PERIOD, "files": [upload("КЖ спорт зал.xlsx")]})
        changed = [r[:] for r in RS_ROWS]
        changed[5][5] = 210000
        self.client.post(up, {**PERIOD, "quarter": "3", "files": [upload("b.xlsx", xlsx(changed))]})
        self.url = reverse("panel:base_data")

    def get(self, **params):
        return self.client.get(self.url, params)

    def test_tree_tabs_and_groups(self):
        page = self.get()
        self.assertContains(page, "Тошкент шаҳри")
        self.assertContains(page, '<span class="gt">Машина ва механизмлар</span>')  # ресурс тури гуруҳи
        self.assertNotContains(page, '<h3 title=')  # ЛРВ юкланмаган — «Иш тури» фильтри йўқ
        self.assertContains(page, "6 қатор")
        page = self.get(region="toshkent_sh", year=2025, quarter=2)
        self.assertContains(page, "КЖ спорт зал.xlsx")  # дарахтда очилган даврнинг файллари
        self.assertContains(page, "1–3 / 3 қатор")

    def test_filters(self):
        self.assertContains(self.get(cat="J"), "1–2 / 2 қатор")
        self.assertEqual(sorted(float(o.price) for o in self.get(cat="J", quarter=2, year=2025, region="toshkent_sh")
                                .context["page"]), [207913.0])
        src = Source.objects.get(title="b.xlsx")
        self.assertContains(self.get(region="toshkent_sh", year=2025, quarter=3, src=src.pk), "1–3 / 3 қатор")
        self.assertContains(self.get(q="ВОДА"), "1–2 / 2 қатор")
        self.assertContains(self.get(q="000258"), "1–2 / 2 қатор")
        self.assertContains(self.get(q="йўқ-нарса"), "бўйича ҳеч нарса топилмади")

    def test_pagination_and_sort(self):
        page = self.get(per=25, sort="-price")
        self.assertContains(page, "1–6 / 6 қатор")
        rows = list(page.context["page"])
        self.assertEqual([float(o.price) for o in rows][:2], [210000.0, 207913.0])
        self.assertEqual(self.get(page=99).context["page"].number, 1)  # чегарадан ташқари — охирги саҳифа
        page = self.get(per=25, page=1)
        self.assertNotContains(page, "page=2")  # битта саҳифа — саҳифалар рўйхати йўқ

    def test_upload_redirects_to_period(self):
        r = self.client.post(reverse("panel:base_upload"), {**PERIOD, "files": [upload("c.xlsx")]})
        self.assertEqual(r.url, reverse("panel:base_data") + "?region=toshkent_sh&year=2025")


def lrv(works=None) -> bytes:
    """bv*.xlsx: ЛРВ — ishlar (shifr) va ularning resurslari (norma, jami)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "_ЛРВ"
    ws.append(["N п.п.", "Шифр номера нормативов и коды ресурсов", "Наименование работ и затрат", "Единица измерения",
               "Количество"])
    ws.append([None, None, None, None, "на. ед. измерения", "по проектным данным"])
    ws.append([1, 2, 3, 4, 5, 6])
    for row in works or [
        [1, "Е0101-197-02", "РАЗРАБОТКА ГРУНТА", "1000 М3", 1.5],
        [1.1, "1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 5.86, 8.79],
        [1.2, "258", "БУЛЬДОЗЕРЫ 79 КВТ", "МАШ.-Ч", 3.92, 5.88],
        [2, "Е2701-005-01", "УСТРОЙСТВО ОСНОВАНИЯ", "1000М2", 2],
        [2.1, "1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 34.7, 69.4],
        [2.2, "9219", "ВОДА", "М3", 10.5, 21],
        [3, None, "ПЕСЧАНО-ГРАВИЙНАЯ СМЕСЬ", "М3", 88.2],
    ]:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class WorksTests(TestCase):
    """ЛРВ: иш тури шифрдан (Е01 …), bp↔bv жуфти, нормалар саҳифаси ва такрорлар."""

    def setUp(self):
        shutil.rmtree(TMP_MEDIA / services.UPLOAD_DIR, ignore_errors=True)
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        self.client.force_login(self.admin)
        self.up = reverse("panel:base_upload")

    def post(self, *files, **period):
        return self.client.post(self.up, {**PERIOD, **period, "files": list(files)}, follow=True)

    def books(self):
        return dict(Observation.objects.values_list("resource__code", "books"))

    def test_pair_links_books_both_orders(self):
        page = self.post(upload("bv1010.xlsx", lrv()))
        self.assertContains(page, "2 та иш")
        bv = Source.objects.get()
        self.assertEqual((bv.stats["taken"], bv.stats["works"], bv.stats["books"]), (0, 2, {"Е01": 1, "Е27": 1}))
        self.post(upload("bp1010.xlsx"))  # нархлар кейин келди — жуфти топилади
        self.assertEqual(self.books(), {"1": ",Е01,Е27,", "258": ",Е01,", "9219": ",Е27,"})
        changed = [r[:] for r in RS_ROWS]
        changed[5][5] = 210000
        self.post(upload("bp2000.xlsx", xlsx(changed)))  # жуфти йўқ — иш тури аниқланмаган
        self.assertEqual(Observation.objects.filter(source__title="bp2000.xlsx", books="").count(), 3)
        # bv ўчирилса — жуфтининг иш турлари тозаланади
        self.client.post(reverse("panel:base_source", args=[bv.pk, "delete"]))
        self.assertEqual(set(self.books().values()), {""})

    def test_pair_only_same_period(self):
        self.post(upload("bp1010.xlsx"))
        self.post(upload("bv1010.xlsx", lrv()), year="2026")
        self.assertEqual(set(self.books().values()), {""})
        bv = Source.objects.get(title="bv1010.xlsx")
        self.client.post(reverse("panel:base_source", args=[bv.pk, "edit"]), {**PERIOD})  # даври тўғриланди
        self.assertEqual(self.books()["258"], ",Е01,")

    def test_one_file_with_both_sheets(self):
        wb = Workbook()
        rs_sheet(wb.active, RS_ROWS)
        ws = wb.create_sheet("Локально")
        from openpyxl import load_workbook
        src = load_workbook(io.BytesIO(lrv())).active
        for row in src.iter_rows(values_only=True):
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        self.post(upload("2. ЭО.xlsx", buf.getvalue()))
        self.assertEqual(self.books()["258"], ",Е01,")

    def test_data_page_book_filter(self):
        self.post(upload("bv1010.xlsx", lrv()))
        self.post(upload("bp1010.xlsx"))
        url = reverse("panel:base_data")
        page = self.client.get(url)
        self.assertContains(page, 'title="Земляные работы"')
        self.assertContains(self.client.get(url, {"book": "Е01"}), "1–2 / 2 қатор")
        self.assertContains(self.client.get(url, {"book": "Е27"}), "1–2 / 2 қатор")
        self.assertContains(self.client.get(url, {"book": "-"}), "0 қатор")  # bp1010 нинг ҳамма коди bv1010 ишларида бор
        self.assertContains(self.client.get(url, {"book": "Е01", "cat": "J"}), "1–1 / 1 қатор")

    def test_norms_merge_duplicates_and_variants(self):
        """Бир шифр 3 файлда: бир хил норма — бир қатор «3 файлда»; бир файлда бошқача — «бошқача норма»."""
        self.post(upload("bv1.xlsx", lrv()))
        self.post(upload("bv2.xlsx", lrv([[1, "Е0101-197-02", "РАЗРАБОТКА ГРУНТА", "1000 М3", 3],
                                          [1.1, "1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 5.86, 17.58],
                                          [1.2, "258", "БУЛЬДОЗЕРЫ 79 КВТ", "МАШ.-Ч", 3.92, 11.76]])))
        self.post(upload("bv3.xlsx", lrv([[1, "Е0101-197-02 ДОП. 11", "РАЗРАБОТКА ГРУНТА", "1000 М3", 1],
                                          [1.1, "1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 6.1, 6.1],
                                          [1.2, "258", "БУЛЬДОЗЕРЫ 79 КВТ", "МАШ.-Ч", 3.92, 3.92]])))
        page = self.client.get(reverse("panel:base_norms"))
        self.assertContains(page, "Е0101-197-02")
        self.assertContains(page, "2 та норма")  # Е0101-197-02 ва Е2701-005-01
        self.assertContains(self.client.get(reverse("panel:base_norms"), {"book": "Е27"}), "1 та норма")
        self.assertContains(self.client.get(reverse("panel:base_norms"), {"grp": "build"}), "2 та норма")
        self.assertContains(self.client.get(reverse("panel:base_norms"), {"grp": "mount"}), "0 та норма")
        d = self.client.get(reverse("panel:base_norm", args=["Е0101-197-02"])).context["d"]
        self.assertEqual((d["n_files"], d["n_uses"], d["n_variants"]), (3, 3, 1))
        rows = {r["code"]: (str(r["norma"]), r["n"], [(str(n), c) for n, c in r["others"]]) for r in d["rows"]}
        self.assertEqual(rows, {"1": ("5.86", 2, [("6.1", 1)]), "258": ("3.92", 3, [])})
        d27 = self.client.get(reverse("panel:base_norm", args=["Е2701-005-01"])).context["d"]
        self.assertEqual([(e["name"], e["files"]) for e in d27["extras"]], [("ПЕСЧАНО-ГРАВИЙНАЯ СМЕСЬ", 1)])
        self.assertEqual(self.client.get(reverse("panel:base_norm", args=["Е99-1-1"])).status_code, 404)

    def test_resource_page_works_and_file_link(self):
        """Resurs sahifasi: qaysi ishlarda (shifr, norma), fayl manbasi — «Маълумотлар»ga havola (smeta emas)."""
        self.post(upload("bv1010.xlsx", lrv()))
        self.post(upload("bp1010.xlsx"))
        page = self.client.get(reverse("panel:base_resource", args=["258"]))
        self.assertContains(page, "Е0101-197-02")
        self.assertContains(page, "Машина ва механизмлар")
        self.assertEqual([w["work__shifr"] for w in page.context["works"]], ["Е0101-197-02"])
        src = Source.objects.get(title="bp1010.xlsx")
        self.assertContains(page, f"src={src.pk}")
        self.assertNotContains(page, f"/job/{src.work_id}")

    def test_duplicate_lrv_file(self):
        self.post(upload("bv1.xlsx", lrv()))
        page = self.post(upload("bv1 (1).xlsx", lrv()))
        self.assertContains(page, "Такрор файллар — қарор кутмоқда (1)")
        self.assertEqual(Source.objects.count(), 1)


class OffersTests(TestCase):
    """Фойдаланувчи нархлаган сметалар — админ белгилаб, давр танлаб базага киритади."""

    def setUp(self):
        from .tests import make_job, row
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        user = User.objects.create_user("user@test.uz", PASSWORD, first_name="U", last_name="V")
        self.a = make_job(user, [row(11, "258", "БУЛЬДОЗЕРЫ 79 КВТ", "МАШ.-Ч", 200000, cat="J")], name="a.xlsx")
        self.b = make_job(user, [row(11, "258", "БУЛЬДОЗЕРЫ 79 КВТ", "МАШ.-Ч", 210000, cat="J")], name="b.xlsx")
        self.client.force_login(self.admin)

    def test_select_and_add(self):
        page = self.client.get(reverse("panel:base_sources"))
        self.assertContains(page, "Янги нархланган сметалар (2)")
        url = reverse("panel:base_add_selected")
        self.client.post(url, {"items": [f"smeta:{self.a.pk}"]})  # давр танланмаган — киритилмайди
        self.assertFalse(Source.objects.exists())
        self.client.post(url, {**PERIOD, "items": [f"smeta:{self.a.pk}", "file:00000000-0000-0000-0000-000000000000"]})
        s = Source.objects.get()
        self.assertEqual((s.work_id, s.region, s.year, s.quarter), (self.a.pk, "toshkent_sh", 2025, None))
        self.assertContains(self.client.get(reverse("panel:base_sources")), "Янги нархланган сметалар (1)")


class SmetaWorksTests(TestCase):
    """Нархланган сметадан: нархлар + ишлар (шифр, норма), манба саҳифаси, нарх қаторида шифр."""

    def setUp(self):
        from .tests import make_job, row
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        user = User.objects.create_user("user@test.uz", PASSWORD, first_name="U", last_name="V")
        rows = [
            row(17, None, "ЗЕМЛЯНЫЕ РАБОТЫ", None, "ТОПИЛМАДИ", status="NOT_FOUND"),
            row(18, "Е0101-197-02 ДОП. 11", "РАЗРАБОТКА ГРУНТА", "1000 М3", 0, status="WORK_HEADER", num="1", qty=0.32),
            row(19, "1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 36030.1, cat="I", num="1.1", qty_unit=5.86, qty_total=1.88),
            row(20, "258", "БУЛЬДОЗЕРЫ 79 КВТ", "МАШ.-Ч", 203442, cat="J", num="1.2", qty_unit=3.92, qty_total=1.25),
            row(21, "Е2701-005-01", "УСТРОЙСТВО ОСНОВАНИЯ", "1000М2", 0, status="WORK_HEADER", num="2", qty=0.88),
            row(22, "1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 36030.1, cat="I", num="2.1", qty_unit=34.7, qty_total=30.5),
            row(23, None, "ПЕСЧАНО-ГРАВИЙНАЯ СМЕСЬ", "М3", 120000, status="NOT_FOUND", num="3", qty=88.2),
        ]
        self.job = make_job(user, rows, name="КЖ.xlsx")
        self.client.force_login(self.admin)
        self.client.post(reverse("panel:base_add_selected"), {**PERIOD, "items": [f"smeta:{self.job.pk}"]})
        self.src = Source.objects.get()

    def test_works_and_books_from_smeta(self):
        st = self.src.stats
        self.assertEqual((st["taken"], st["works"], st["books"]), (2, 2, {"Е01": 1, "Е27": 1}))
        self.assertEqual(st["skipped"], {"duplicate": 1, "no_code": 1})
        self.assertEqual(st["detail"][0]["works"], 2)
        w = self.src.works.get(shifr="Е0101-197-02")
        self.assertEqual([(r.code, float(r.norma), float(r.qty), r.extra) for r in w.resources.all()],
                         [("1", 5.86, 1.88, False), ("258", 3.92, 1.25, False)])
        extra = self.src.works.get(shifr="Е2701-005-01").resources.get(extra=True)
        self.assertEqual((extra.name, extra.norma, float(extra.qty)), ("ПЕСЧАНО-ГРАВИЙНАЯ СМЕСЬ", None, 88.2))
        self.assertEqual(dict(Observation.objects.values_list("resource__code", "books")), {"1": ",Е01,Е27,", "258": ",Е01,"})

    def test_source_page_and_shifr_in_prices(self):
        page = self.client.get(reverse("panel:base_source_detail", args=[self.src.pk]))
        self.assertContains(page, "Нега олинмади")
        self.assertContains(page, "бир сметадан бир код бир марта олинади")
        self.assertContains(page, "Е27 · Автомобильные дороги · 1")
        data = self.client.get(reverse("panel:base_data"), {"cat": "J"})
        self.assertContains(data, ">Е0101-197-02</a>")
        self.assertContains(self.client.get(reverse("panel:base_resource", args=["1"])), "та нархнинг ўртадагиси")


class BookNamesTests(TestCase):
    """Тўплам номлари: фақат админ ёзгани; ёзилмаган — бўш (кодининг ўзи кўринади)."""

    def setUp(self):
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        self.client.force_login(self.admin)

    def test_edit_and_reset(self):
        self.assertEqual(services.book_label("Ц38"), "")
        url = reverse("panel:base_books")
        self.client.post(url, {"name_Ц38": "Лифтлар монтажи", "name_Е01": "", "name_X1": "бузуқ"})
        self.assertEqual(services.book_label("Ц38"), "Лифтлар монтажи")
        self.assertEqual(services.book_label("Е01"), "")
        from .models import BookName
        self.assertEqual(list(BookName.objects.values_list("key", flat=True)), ["Ц38"])
        self.assertContains(self.client.get(url), "Лифтлар монтажи")
        self.client.post(url, {"name_Ц38": ""})
        self.assertEqual(services.book_label("Ц38"), "")

    def test_how_page(self):
        page = self.client.get(reverse("panel:base_how"))
        self.assertContains(page, "Ўрта нарх қандай топилади")
        self.assertContains(page, "Е0101-197-02")
        self.assertEqual(len(page.context["steps"]), 8)
