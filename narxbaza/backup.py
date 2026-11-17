"""Нарх базасининг тўлиқ нусхаси (ZIP) ва ундан тиклаш.

ZIP ичида:
* ``manifest.json`` — қайси дастур, қачон, нечта ёзув, охирги миграция;
* ``data.json`` — ҳамма жадваллар (ресурслар, нархлар, тўпламлар, ҳисоблар, тарих, текширувлар, созлама),
  саватчадагилари ҳам;
* ``media/…`` — тўпламларнинг асл файллари, текширувларнинг асл юкланган файллари ва Excel кўринишлари.

Тиклаш базадаги ҳамма нарх базаси маълумотини нусхадагиси билан **алмаштиради** (қўшмайди). Олдин жорий
ҳолатнинг нусхаси ``MEDIA_ROOT/narxbaza/backups/`` га автоматик сақланади — адашиб тикланса, қайтариш мумкин.
Нусхадаги фойдаланувчи бу серверда бўлмаса — «ким» майдонлари бўш қолади (маълумот йўқолмайди).
"""
from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.management.color import no_style
from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from .models import Alias, Batch, Config, DupIgnore, Intake, IntakeRow, Item, Log, Price, Stat

log = logging.getLogger(__name__)
APP = "narxbaza"
VERSION = 1
# сақлаш тартиби (боғланганлари кейин); ўчириш — тескари
MODELS = [Config, Item, Batch, Price, Stat, Alias, DupIgnore, Log, Intake, IntakeRow]


class BackupError(ValueError):
    pass


def _media() -> Path:
    return Path(settings.MEDIA_ROOT)


def _last_migration() -> str:
    return (MigrationRecorder.Migration.objects.filter(app=APP).order_by("-applied", "-id")
            .values_list("name", flat=True).first() or "")


def _media_files() -> list[str]:
    """Нусхага кирадиган файллар (MEDIA_ROOT ичидаги нисбий йўллар, бор бўлганлари)."""
    rels = set(Batch.all_objects.exclude(file_path="").values_list("file_path", flat=True))
    # текширувлар (бекор қилинганидан ташқари): Excel кўринишлари ва асл юкланган файллар — тиклангандан кейин ҳам очилсин
    for files, fp, pr in Intake.objects.exclude(status=Intake.Status.CANCELLED).values_list("files", "file_path", "progress"):
        rels.update(f.get("path") for f in files or [] if isinstance(f, dict) and f.get("path"))
        rels.add(fp)
        rels.update(x[1] for x in (pr or {}).get("src") or [] if isinstance(x, (list, tuple)) and len(x) == 3)
    root = _media().resolve()
    out = []
    for r in sorted(x for x in rels if x and x.startswith(APP + "/")):
        p = (root / r).resolve()
        if p.is_file() and root in p.parents:
            out.append(r)
    return out


def counts() -> dict:
    return {m._meta.model_name: m._base_manager.count() for m in MODELS}


def export(dest: Path, with_files=True) -> dict:
    """Нусхани ``dest`` (.zip) га ёзади; manifest'ни қайтаради. ``with_files=False`` — фақат маълумот (нархлар,
    ресурслар, тарих …), юкланган Excel файлларсиз: бир неча МБ. Тиклашда сервердаги файллар ўчмайди."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    files = _media_files() if with_files else []
    manifest = {"app": APP, "version": VERSION, "created": timezone.now().isoformat(),
                "migration": _last_migration(), "counts": counts(), "files": len(files), "with_files": with_files,
                # бошқа серверда фойдаланувчи рақамлари бошқача — почтаси бўйича топилади
                "users": {str(pk): email for pk, email in get_user_model().objects.values_list("pk", "email")}}
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        with z.open("data.json", "w", force_zip64=True) as fh:
            fh.write(b"[")
            first = True
            for m in MODELS:
                qs = m._base_manager.order_by("pk")
                for i in range(0, qs.count(), 2000):
                    chunk = serializers.serialize("json", qs[i:i + 2000], ensure_ascii=False).strip()[1:-1].strip()
                    if chunk:
                        fh.write((("" if first else ",") + chunk).encode("utf-8"))
                        first = False
            fh.write(b"]")
        for r in files:
            z.write(_media() / r, "media/" + r)
    log.info("narxbaza: нусха олинди — %s (%s)", dest.name, manifest["counts"])
    return manifest


def read_manifest(zpath: Path) -> dict:
    try:
        with zipfile.ZipFile(zpath) as z:
            names = set(z.namelist())
            if not {"manifest.json", "data.json"} <= names:
                raise BackupError("Бу нарх базасининг нусхаси эмас (manifest.json / data.json йўқ).")
            m = json.loads(z.read("manifest.json"))
    except zipfile.BadZipFile as e:
        raise BackupError("ZIP файл бузуқ.") from e
    if m.get("app") != APP:
        raise BackupError("Бу нарх базасининг нусхаси эмас.")
    if int(m.get("version") or 0) > VERSION:
        raise BackupError("Нусха дастурнинг янгироқ версиясида олинган — аввал дастурни янгиланг.")
    return m


def _user_map(manifest: dict) -> dict:
    """Нусхадаги фойдаланувчи рақами → шу сервердаги рақами (почтаси бўйича; эски нусхада — рақамнинг ўзи)."""
    local = dict(get_user_model().objects.values_list("email", "pk"))
    src = manifest.get("users")
    if isinstance(src, dict):
        return {int(pk): local.get(email) for pk, email in src.items() if str(pk).isdigit()}
    return {pk: pk for pk in local.values()}


def _uid(umap: dict, v):
    return umap.get(v) if v is not None else None


def _fix_users(obj, umap: dict) -> None:
    for f in obj._meta.concrete_fields:
        if f.is_relation and f.related_model is get_user_model():
            setattr(obj, f.attname, _uid(umap, getattr(obj, f.attname)))


def backups_dir() -> Path:
    return _media() / APP / "backups"


def restore(zpath: Path, user=None) -> dict:
    """Нусхадан тиклаш (ҳаммаси алмашади). Олдин жорий ҳолат ``backups/`` га сақланади. manifest'ни қайтаради."""
    m = read_manifest(zpath)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    safety = backups_dir() / f"tiklashdan-oldin-{stamp}.zip"
    export(safety, with_files=False)  # файллар серверда қолади — нусхага керак эмас
    umap = _user_map(m)
    root = _media().resolve()
    with zipfile.ZipFile(zpath) as z:
        raw = z.read("data.json").decode("utf-8")
        try:
            objs = list(serializers.deserialize("json", raw, ignorenonexistent=True))
        except Exception as e:  # noqa: BLE001
            raise BackupError(f"Нусхадаги маълумотни ўқиб бўлмади: {str(e)[:200]}") from e
        with transaction.atomic():
            for mdl in reversed(MODELS):
                mdl._base_manager.all().delete()
            for o in objs:
                _fix_users(o.object, umap)
                o.save()
            with connection.cursor() as c:
                for sql in connection.ops.sequence_reset_sql(no_style(), MODELS):
                    c.execute(sql)
        for name in z.namelist():
            if not name.startswith("media/") or name.endswith("/"):
                continue
            rel = PurePosixPath(name[len("media/"):])
            if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0] != APP:
                continue
            target = (root / Path(*rel.parts)).resolve()
            if root not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(target, "wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
    log.info("narxbaza: нусхадан тикланди — %s (олдинги ҳолат: %s), %s", zpath.name, safety.name, m.get("counts"))
    m["safety"] = safety.name
    return m


def saved() -> list[dict]:
    """Серверда сақланган нусхалар (тиклашдан олдин автоматик олинганлари) — янгиси аввал."""
    d = backups_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
        out.append({"name": p.name, "size": p.stat().st_size,
                    "time": timezone.datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.get_current_timezone())})
    return out


# ===================== қўшиш (бирлаштириш) =====================
MERGE_RULES = [
    "Ресурс номи + бирлиги бўйича топилади (катта-кичик ҳарф, бўшлиқ, лотин/кирилл ўхшаш ҳарфлар фарқ қилмайди) "
    "ёки бошқа номи орқали. Бор бўлса — нархлари шунга қўшилади, йўқ бўлса — янги ресурс очилади.",
    "Тоифа: сиз тасдиқлаган ресурснинг тоифаси ўзгармайди. Сизда тасдиқланмаган, нусхада тасдиқланган бўлса — "
    "нусхадагиси олинади. Иккаласида ҳам тасдиқланган ва бошқа-бошқа бўлса — сизники қолади, рўйхатда кўрсатилади.",
    "Тўплам ўз вилояти ва санаси билан алоҳида тўплам бўлиб қўшилади. Шу тўплам аввал қўшилган бўлса "
    "(бир нусхани икки марта юкласангиз) — қайта қўшилмайди.",
    "Нарх: шу ресурс, шу вилоят, шу сана ва шу нарх базада бўлса — иккинчи марта ёзилмайди.",
    "Сиз саватчага ташлаган ресурс тикланмайди — унинг нархлари олинмайди. Нусхадаги саватчадагилар ҳам олинмайди.",
    "Бошқа номлар қўшилади; шу ном сизда бошқа ресурсники бўлса — сизники қолади.",
    "Текширувлар (тасдиқлаб йиғиш жараёни) ва созламалар олинмайди — улар шу серверники.",
    "Паст / ўрта / юқори нарх қўшилган нархлар билан қайта ҳисобланади. Олдин жорий ҳолатнинг нусхаси сақланади.",
]


class _Dry(Exception):
    pass


def merge(zpath: Path, user=None, dry_run=False) -> dict:
    """Нусхани жорий базага **қўшади** (``MERGE_RULES``). ``dry_run`` — ҳеч нарса ёзмай, натижасини айтади."""
    from collections import defaultdict
    from decimal import Decimal
    from . import services as S
    m = read_manifest(zpath)
    rep = defaultdict(int)
    for k in ("items_new", "items_matched", "items_cat", "aliases_new", "batches_new", "batches_same", "prices_new",
              "prices_dup", "prices_skipped", "deleted_skipped", "dupignore_new", "groups", "files"):
        rep[k] = 0
    rep["cat_conflicts"], rep["alias_conflicts"], rep["trash_items"] = [], [], []
    umap = _user_map(m)
    safety, files_to_copy = None, []
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    with zipfile.ZipFile(zpath) as z:
        try:
            objs = [o.object for o in serializers.deserialize("json", z.read("data.json").decode("utf-8"),
                                                              ignorenonexistent=True)]
        except Exception as e:  # noqa: BLE001
            raise BackupError(f"Нусхадаги маълумотни ўқиб бўлмади: {str(e)[:200]}") from e
        by = defaultdict(list)
        for o in objs:
            by[type(o)].append(o)
        rep["intakes_skipped"] = len(by[Intake])
        if not dry_run:
            safety = backups_dir() / f"qoshishdan-oldin-{stamp}.zip"
            export(safety, with_files=False)  # файллар серверда қолади — нусхага керак эмас
        try:
            with transaction.atomic():
                _merge_rows(by, rep, umap, user, files_to_copy, S, Decimal, defaultdict)
                if dry_run:
                    raise _Dry
        except _Dry:
            pass
        if not dry_run:
            _copy_files(z, files_to_copy, stamp, rep)
    out = dict(rep)
    out["manifest"], out["safety"] = m, (safety.name if safety else None)
    log.info("narxbaza: нусха қўшилди%s — %s", " (синов)" if dry_run else "",
             {k: v for k, v in out.items() if isinstance(v, int)})
    return out


def _merge_rows(by, rep, umap, user, files_to_copy, S, Decimal, defaultdict):
    now = timezone.now()
    # --- ресурслар ---
    trash_keys = {k for k in Item.all_objects.exclude(deleted_at=None).values_list("key_before", flat=True) if k}
    src_alias = defaultdict(list)
    for a in by[Alias]:
        src_alias[a.item_id].append(a)
    imap, new_items = {}, set()
    for it in by[Item]:
        if it.deleted_at is not None:
            rep["deleted_skipped"] += 1
            continue
        # нусхадаги калитнинг ўзи (жуда узун номда калит тўлиқ номдан — қисқарган номдан қайта ҳисоблаб бўлмайди),
        # кейин номи + бирлигидан ҳисобланган калит, кейин бошқа номлари
        keys = [k for k in (it.key_before or it.key, S.item_key(it.name, it.unit)) if k and not k.startswith("~")]
        keys += [k for a in src_alias[it.pk] for k in (a.key, S.item_key(a.name, a.unit))]
        local = next((o for o in map(S.owner_of_key, dict.fromkeys(keys)) if o is not None), None)
        key = keys[0]
        if local is None and any(k in trash_keys for k in keys[:2]):
            rep["trash_items"].append(f"{it.name} ({it.unit})")
            continue
        if local is None:
            local = Item.objects.create(key=key, name=it.name, unit=it.unit, category=it.category, edited=it.edited,
                                        verified=it.verified, verified_at=it.verified_at,
                                        verified_by_id=_uid(umap, it.verified_by_id))
            new_items.add(local.pk)
            rep["items_new"] += 1
        else:
            rep["items_matched"] += 1
            if local.verified:
                if it.verified and it.category != local.category:
                    rep["cat_conflicts"].append(f"{local.name} ({local.unit}) — сизда «{local.get_category_display()}», "
                                                f"нусхада «{it.get_category_display()}»")
            elif it.verified or (local.category == "O" and it.category != "O"):
                if local.category != it.category:
                    S._log(Log.Action.CATEGORY, user, item=local,
                           detail=f"{local.get_category_display()} → {it.get_category_display()} (нусхадан)",
                           data={"old": local.category, "new": it.category})
                    local.category = it.category
                    rep["items_cat"] += 1
                if it.verified:
                    local.verified, local.verified_at = True, it.verified_at or now
                    local.verified_by_id = _uid(umap, it.verified_by_id)
                local.save()
        imap[it.pk] = local.pk
    # --- бошқа номлар ---
    for a in by[Alias]:
        if a.item_id not in imap:
            continue
        key = a.key or S.item_key(a.name, a.unit)
        owner = S.owner_of_key(key)
        if owner is None:
            Alias.objects.create(key=key, item_id=imap[a.item_id], name=a.name, unit=a.unit,
                                 added_by_id=_uid(umap, a.added_by_id))
            rep["aliases_new"] += 1
        elif owner.pk != imap[a.item_id]:
            rep["alias_conflicts"].append(f"{a.name} ({a.unit})")
    # --- тўпламлар ва нархлар ---
    sig = lambda b: (b.created, b.title, b.region, b.date, b.kind)  # noqa: E731
    have_b = {sig(b) for b in Batch.all_objects.all()}
    have_p = set(Price.all_objects.filter(item_id__in=set(imap.values()))
                 .values_list("item_id", "region", "date", "price"))
    prices_of = defaultdict(list)
    for p in by[Price]:
        prices_of[p.batch_id].append(p)
    touched = set()
    q4 = Decimal("0.0001")
    for b in by[Batch]:
        if b.deleted_at is not None:
            rep["deleted_skipped"] += 1
            continue
        if sig(b) in have_b:
            rep["batches_same"] += 1
            continue
        new = []
        for p in prices_of[b.pk]:
            if p.item_id not in imap:
                rep["prices_skipped"] += 1
                continue
            k = (imap[p.item_id], p.region, p.date, Decimal(p.price).quantize(q4))
            if k in have_p:
                rep["prices_dup"] += 1
                continue
            have_p.add(k)
            new.append((p, k[0]))
        if not new:
            rep["batches_same"] += 1
            continue
        nb = Batch.objects.create(kind=b.kind, work_id=b.work_id, title=b.title, region=b.region, date=b.date,
                                  note=b.note, stats=b.stats, owner_id=_uid(umap, b.owner_id),
                                  added_by_id=_uid(umap, b.added_by_id), file_path=b.file_path)
        Batch.all_objects.filter(pk=nb.pk).update(created=b.created)  # шу тўплам қайта қўшилмаслиги учун
        if b.file_path:
            files_to_copy.append((nb.pk, b.file_path))
        Price.all_objects.bulk_create([
            Price(item_id=iid, batch=nb, price=p.price, qty=p.qty, region=p.region, date=p.date, year=p.year,
                  name=p.name, unit=p.unit, places=p.places, seen=p.seen, excluded=p.excluded, edited=p.edited, pin=p.pin,
                  note=p.note) for p, iid in new], batch_size=1000)
        touched.update((iid, p.region, p.year) for p, iid in new)
        rep["batches_new"] += 1
        rep["prices_new"] += len(new)
    # --- «бошқа-бошқа ресурс» белгилари, янги ресурслар тарихи ---
    for d in by[DupIgnore]:
        a, b2 = imap.get(d.a_id), imap.get(d.b_id)
        if a and b2 and a != b2:
            _, made = DupIgnore.objects.get_or_create(a_id=min(a, b2), b_id=max(a, b2))
            rep["dupignore_new"] += int(made)
    for lg in by[Log]:
        if lg.item_id and imap.get(lg.item_id) in new_items:
            Log.objects.create(action=lg.action, item_id=imap[lg.item_id], title=lg.title, detail=lg.detail,
                               data=lg.data, user_id=_uid(umap, lg.user_id))
    S.recalc(touched)
    rep["groups"] = len(touched)


def _copy_files(z, files_to_copy, stamp, rep):
    root = _media().resolve()
    names = set(z.namelist())
    for bpk, rel in files_to_copy:
        src = "media/" + rel
        if src not in names or not rel.startswith(APP + "/"):
            continue
        target = (root / rel).resolve()
        if root not in target.parents:
            continue
        if target.exists():
            if target.stat().st_size == z.getinfo(src).file_size:
                continue  # ўша файл
            rel = f"{APP}/import/{stamp}/{rel[len(APP) + 1:]}"
            target = (root / rel).resolve()
            Batch.all_objects.filter(pk=bpk).update(file_path=rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        with z.open(src) as fs, open(target, "wb") as fd:
            while chunk := fs.read(1 << 20):
                fd.write(chunk)
        rep["files"] += 1
