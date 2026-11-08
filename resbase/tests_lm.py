"""Ускуна ва кабель базаси: номдан тахмин, луғат (код / ном / ўхшаш), ўзи ўрганиш, админ ўргатиши, панел."""
import shutil
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from accounts.models import User
from core.classify import CABLE, EQUIPMENT, OTHER, name_hint
from core.lmbase import LMBase, lm_key
from core.pipeline import _category_hints, _Priced

from . import lm
from .models import LMEntry, LMVote

PASSWORD = "Kuchli-parol-2026"
TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_lm_")


class NameHintTests(SimpleTestCase):
    def test_equipment_words(self):
        for n in ("ЩИТ СИЛОВОЙ РАСПРЕДЕЛИТЕЛЬНЫЙ ЩРВ-48", "Извещатель пожарный дымовой ИП-212", "IP-ВИДЕОКАМЕРА 4МП",
                  "ФАНКОЙЛ КАССЕТНЫЙ", "Стол для посетителя", "Коммутатор 24 порта"):
            self.assertEqual((name_hint(n) or ("",))[0], EQUIPMENT, n)

    def test_cable_only_when_name_starts_with_it(self):
        for n in ("КАБЕЛЬ ВВГНГ 3Х2,5", "ПРОВОД ПВ1 1Х1,5", "Силовой кабель АВВГ 4х16"):
            self.assertEqual((name_hint(n) or ("",))[0], CABLE, n)
        for n in ("НАКОНЕЧНИКИ КАБЕЛЬНЫЕ МЕДНЫЕ", "КАБЕЛЬНЫЙ ЛОТОК PNK-200", "ХОМУТ КАБЕЛЬНЫЙ 4,8Х200",
                  "АСБЕСТОВЫЙ ШНУР ШАОН", "ПРОВОЛОКА ВЯЗАЛЬНАЯ"):
            self.assertIsNone(name_hint(n), n)

    def test_not_equipment_lookalikes(self):
        for n in ("ЩИТЫ ИЗ ДОСОК ТОЛЩИНОЙ 25 ММ", "ВЕРТИКАЛЬНЫЕ ОПОРНЫЕ СТОЛБЫ", "КРАН ШАРОВОЙ ДЛЯ ФАНКОЙЛА",
                  "КЛАПАН ТЕРМОСТАТИЧЕСКИЙ В КОМПЛЕКТЕ С ФАНКОЙЛОМ", "МЕТАЛЛОКОНСТРУКЦИИ ЩИТОВ ИЗ УГОЛКА"):
            self.assertIsNone(name_hint(n), n)


class LMBaseTests(SimpleTestCase):
    def setUp(self):
        self.base = LMBase([
            {"key": lm_key("Вентилятор канальный ВК-125"), "name": "Вентилятор канальный ВК-125", "code": "",
             "category": "K", "reason": "базада", "strong": True},
            {"key": lm_key("Блок сигнально-пусковой С2000-СП1"), "name": "Блок сигнально-пусковой С2000-СП1",
             "code": "004512", "category": "M", "reason": "базада: 3 та RES'да", "strong": True},
        ])

    def test_code_name_fuzzy(self):
        self.assertEqual(self.base.lookup("бошқа ном", "4512").how, "code")
        hit = self.base.lookup("БЛОК СИГНАЛЬНО ПУСКОВОЙ  С2000-СП1")
        self.assertEqual((hit.kind, hit.how, hit.strong), (EQUIPMENT, "name", True))
        hit = self.base.lookup("Блок сигнально-пусковой С2000-СП2")
        self.assertEqual((hit.kind, hit.how, hit.strong), (EQUIPMENT, "fuzzy", False))
        self.assertIn("базадаги", hit.reason)
        self.assertEqual(self.base.lookup("Вентилятор канальный ВК-125").kind, OTHER)
        self.assertIsNone(self.base.lookup("Песок строительный"))


def _sheet(names):
    wb = Workbook()
    ws = wb.active
    for r, n in names.items():
        ws.cell(row=r, column=4, value=n)
    return ws


class CategoryHintTests(SimpleTestCase):
    """L/M таклифи тартиби: RES бўлими → ўхшаш RES ресурси бўлими → база → номдаги сўз."""

    def _hints(self, names, base=None, **priced):
        p = _Priced({}, [], min(names), max(names), {r: "NOT_FOUND" for r in names})
        for k, v in priced.items():
            setattr(p, k, v)
        return _category_hints(_sheet(names), p, {r: OTHER for r in names}, 4, base)

    def test_fuzzy_res_resource_section_is_used(self):
        h = self._hints({10: "Щит ЩРН-48 спец"}, near_sections={10: (EQUIPMENT, "ОБОРУДОВАНИЕ", "ЩИТ ЩРН-48")})
        self.assertEqual(h[10][0], EQUIPMENT)
        self.assertIn("ОБОРУДОВАНИЕ", h[10][1])

    def test_base_beats_word_and_blocks_false_hint(self):
        base = LMBase([{"key": lm_key("Насос-дозатор ручной"), "category": "K", "reason": "базада"},
                       {"key": lm_key("Табло Выход"), "category": "M", "reason": "базада: админ"}])
        h = self._hints({10: "Насос-дозатор ручной", 11: "ТАБЛО ВЫХОД", 12: "Насос циркуляционный"}, base)
        self.assertNotIn(10, h)  # базада «ускуна эмас» — «насос» сўзи ҳам таклиф бермайди
        self.assertEqual(h[11][::2], (EQUIPMENT, False))  # базада, лекин ишончли эмас — «Автоматик» ўзи қўймайди
        self.assertEqual(h[12], (EQUIPMENT, "номида «насос…»", True))

    def test_res_material_section_overridden_only_by_admin(self):
        strong = LMBase([{"key": lm_key("Светильник LED 36W"), "category": "M", "reason": "админ", "strong": True}])
        weak = LMBase([{"key": lm_key("Светильник LED 36W"), "category": "M", "reason": "1 та RES", "strong": False}])
        sec = {10: (OTHER, "МАТЕРИАЛЫ")}
        self.assertEqual(self._hints({10: "Светильник LED 36W"}, strong, sections=sec)[10][0], EQUIPMENT)
        self.assertEqual(self._hints({10: "Светильник LED 36W"}, weak, sections=sec), {})

    def test_base_priced_row_still_gets_hint(self):
        h = self._hints({10: "Извещатель дымовой"}, sections={10: (OTHER, "ресурс базаси")})
        self.assertEqual(h[10][0], EQUIPMENT)


def _job(rows, sections=None, categories=None, owner=1, accepted=None):
    return SimpleNamespace(id=uuid.uuid4(), owner_id=owner, lm_accepted=accepted, res_path="", mode="smart", status="done", categories=categories or {},
                           result={"res_sections": sections or [], "rows": rows})


def _row(r, name, status="EXACT", sec=None, sug=None, unit="шт", default="K"):
    return {"row": r, "name": name, "unit": unit, "status": status, "res_section": sec, "suggested_category": sug,
            "default_category": default, "code": ""}


SECS = [{"column": "K", "title": "МАТЕРИАЛЫ"}, {"column": "M", "title": "ОБОРУДОВАНИЕ"}, {"column": "L", "title": "КАБЕЛИ"}]


class HarvestTests(TestCase):
    def test_learns_from_res_sections_and_user(self):
        j1 = _job([_row(1, "Щит ЩРН-48", sec=1), _row(2, "Кабель ВВГ 3х2,5", sec=2), _row(3, "Песок", sec=0),
                   _row(4, "Кабельный канал 20х10", sec=0), _row(5, "Насос-дозатор", sec=0),
                   _row(6, "Табло Выход", status="NOT_FOUND"), _row(7, "Затраты труда", sec=0, default="I")],
                  SECS, categories={"6": "M", "5": "K", "7": "L"})
        j1.result["rows"][4]["suggested_category"] = "M"
        lm.harvest_job(j1)
        cat = dict(LMEntry.objects.values_list("key", "category"))
        self.assertEqual(cat[lm_key("Щит ЩРН-48")], "M")
        self.assertEqual(cat[lm_key("Кабель ВВГ 3х2,5")], "L")
        self.assertEqual(cat[lm_key("Насос-дозатор")], "K")  # RES материалда + смета­чи M таклифини K га қайтарди
        self.assertEqual(cat[lm_key("Табло Выход")], "")  # битта смета­чи кўчирди — тасдиқлашни кутади
        self.assertNotIn(lm_key("Песок"), cat)  # оддий материал — базага кирмайди
        self.assertNotIn(lm_key("Затраты труда"), cat)  # иш ҳақи — хато кўчирилган бўлса ҳам
        self.assertNotIn(lm_key("Кабельный канал 20х10"), cat)  # номидан тахмин йўқ — далил ҳам йўқ

    def test_user_choice_counts_per_estimator_and_does_not_spread(self):
        for _ in range(5):  # битта смета­чи 5 та сметада «ВОДА»ни кабелга кўчирган
            lm.harvest_job(_job([_row(1, "Вода питьевая", status="NOT_FOUND")], categories={"1": "L"}, owner=7))
        e = LMEntry.objects.get()
        self.assertEqual((e.category, e.votes), ("", {"user_L": 1}))
        self.assertTrue(lm.needs_confirm(e))
        lm.harvest_job(_job([_row(1, "Вода питьевая", status="NOT_FOUND")], categories={"1": "L"}, owner=8))
        e.refresh_from_db()
        self.assertEqual(e.category, "L")  # иккинчи смета­чи ҳам шундай деди
        base = lm.load_base()
        self.assertEqual(base.lookup("Вода питьевая").kind, CABLE)
        self.assertIsNone(base.lookup("Вода питьевая техническая"))  # смета­чилар танлови ўхшаш номга тарқалмайди
        lm.harvest_job(_job([_row(1, "Щит распределительный ЩР-12", sec=1)], SECS))
        self.assertEqual(lm.load_base().lookup("Щит распределительный ЩР-14").how, "fuzzy")  # RES далили — тарқалади

    def test_accepted_after_recalc_is_not_a_vote(self):
        # қабул қилингандан кейин қайта ҳисобда таклиф йўқолади (қатор аллақачон M да) — lm_accepted эслаб қолади
        j = _job([_row(1, "Табло Выход", status="NOT_FOUND")], categories={"1": "M"}, accepted=[1])
        lm.harvest_job(j)
        self.assertFalse(LMEntry.objects.exists())
        # эски смета (маълумот йўқ): номидаги сўз шу турни айтса — қабул қилинган таклиф деб ҳисобланади
        old = _job([_row(1, "Извещатель ИП-212", status="NOT_FOUND"), _row(2, "Табло Выход", status="NOT_FOUND")],
                   categories={"1": "M", "2": "M"})
        lm.harvest_job(old)
        self.assertEqual(list(LMEntry.objects.values_list("name", flat=True)), ["Табло Выход"])

    def test_res_votes_count_per_file(self):
        jobs = [_job([_row(1, "Щит ЩРН-48", sec=1)], SECS) for _ in range(5)]
        orig = lm.res_source
        lm.res_source = lambda job: "same-file"
        try:
            for j in jobs:
                lm.harvest_job(j)
        finally:
            lm.res_source = orig
        e = LMEntry.objects.get()
        self.assertEqual(e.votes, {"M": 1})  # битта RES файл 5 сметада — битта овоз
        self.assertFalse(lm._strong(e))

    def test_accepting_suggestion_is_not_a_vote_and_recalc_replaces(self):
        j = _job([_row(1, "Извещатель ИП-212", status="NOT_FOUND", sug="M")], SECS, categories={"1": "M"})
        lm.harvest_job(j)
        self.assertFalse(LMEntry.objects.exists())  # таклифни қабул қилиш («Автоматик» ҳам) — база ўзини тасдиқламайди
        j2 = _job([_row(1, "Щит ЩРН-48", sec=1)], SECS)
        lm.harvest_job(j2)
        lm.harvest_job(j2)  # қайта ҳисоб — овоз иккиланмайди
        self.assertEqual(LMEntry.objects.get().votes, {"M": 1})
        j2.result["rows"] = []
        lm.harvest_job(j2)
        self.assertFalse(LMEntry.objects.exists())  # далили қолмаган ўзи ўрганган ёзув ўчади

    def test_conflict_and_admin_lock(self):
        for sec in (1, 1, 0):
            lm.harvest_job(_job([_row(1, "Вентилятор канальный", sec=sec)], SECS))
        e = LMEntry.objects.get()
        self.assertEqual((e.category, e.votes), ("M", {"M": 2, "K": 1}))
        lm.harvest_job(_job([_row(1, "Вентилятор канальный", sec=0)], SECS))
        e.refresh_from_db()
        self.assertEqual(e.category, "")  # 2:2 — аниқ эмас
        lm.teach("ВЕНТИЛЯТОР  канальный", "K")
        lm.harvest_job(_job([_row(1, "Вентилятор канальный", sec=1)], SECS))
        e.refresh_from_db()
        self.assertEqual((e.category, e.locked), ("K", True))  # админ ўргатгани овоздан устун
        lm.unlock(e)
        e.refresh_from_db()
        self.assertEqual(e.category, "M")
        base = lm.load_base()
        self.assertEqual(base.lookup("Вентилятор канальный").kind, EQUIPMENT)


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class PanelLMTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.admin = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="B", last_name="A")
        self.client.force_login(self.admin)

    def _xlsx(self, rows):
        wb = Workbook()
        ws = wb.active
        for r in rows:
            ws.append(r)
        path = tempfile.mktemp(suffix=".xlsx", dir=TMP_MEDIA)
        wb.save(path)
        with open(path, "rb") as fh:
            return SimpleUploadedFile("uskunalar.xlsx", fh.read())

    def test_page_teach_set_delete(self):
        self.assertEqual(self.client.get(reverse("panel:lm")).status_code, 200)
        self.client.post(reverse("panel:lm_teach"), {"name": "Табло ВЫХОД", "unit": "шт", "category": "M"})
        e = LMEntry.objects.get()
        self.assertEqual((e.category, e.locked, e.origin), ("M", True, "admin"))
        r = self.client.get(reverse("panel:lm") + "?q=табло")
        self.assertContains(r, "Табло ВЫХОД")
        self.client.post(reverse("panel:lm_set", args=[e.pk]), {"category": "L"})
        e.refresh_from_db()
        self.assertEqual(e.category, "L")
        self.client.post(reverse("panel:lm_set", args=[e.pk]), {"category": "auto"})
        self.assertFalse(LMEntry.objects.exists())  # овози йўқ — ўзи ўрганганига қайтганда ўчди
        lm.teach("X1 ускуна", "M")
        self.client.post(reverse("panel:lm_delete", args=[LMEntry.objects.get().pk]))
        self.assertFalse(LMEntry.objects.exists())

    def test_excel_preview_then_confirm(self):
        f = self._xlsx([["№", "Наименование", "Ед. изм."], [1, "Видеокамера IP 4МП купольная", "шт"],
                        [2, "Коммутатор 24 порта PoE", "шт"], [3, "Итого", None]])
        r = self.client.post(reverse("panel:lm_upload"), {"file": f, "kind": "M"})
        self.assertEqual(r.status_code, 302)
        page = self.client.get(r["Location"])
        self.assertContains(page, "Видеокамера IP 4МП купольная")
        self.assertNotContains(page, "Итого")
        self.assertFalse(LMEntry.objects.exists())  # тасдиқламагунча ўзгармайди
        r = self.client.post(r["Location"], {"t0": "1", "c0": "M", "c1": "L", "action": "save"})  # 2-си белгиланмаган
        self.assertEqual(list(LMEntry.objects.values_list("name", "category", "origin")),
                         [("Видеокамера IP 4МП купольная", "M", "excel")])

    def test_excel_code_only_from_code_column(self):
        p = Path(TMP_MEDIA) / "codes.xlsx"
        wb = Workbook()
        wb.active.append(["Наименование", "Ед. изм.", "Кол-во", "Код"])
        wb.active.append(["Раздел 1. Электрика", None, None, None])
        wb.active.append(["Щит ЩРН-48", "шт", 100, "23469"])
        wb.active.append(["Извещатель ИП-212", "шт", 12, None])
        wb.save(p)
        self.assertEqual(lm.parse_excel(p, "M"), [
            {"name": "Щит ЩРН-48", "unit": "шт", "code": "23469", "category": "M"},
            {"name": "Извещатель ИП-212", "unit": "шт", "code": "", "category": "M"}])  # миқдор «12» — код эмас
        wb = Workbook()
        wb.active.append(["Щит ЩРН-48", "шт", 100])  # сарлавҳасиз — код олинмайди
        wb.save(p)
        self.assertEqual(lm.parse_excel(p, "M")[0]["code"], "")

    def test_viewer_cannot_change(self):
        viewer = User.objects.create_user("v@test.uz", PASSWORD, first_name="V", last_name="V", is_staff=True)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("panel:lm")).status_code, 200)
        self.assertEqual(self.client.post(reverse("panel:lm_teach"), {"name": "A", "category": "M"}).status_code, 403)
        user = User.objects.create_user("u@test.uz", PASSWORD, first_name="U", last_name="U")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("panel:lm")).status_code, 404)
