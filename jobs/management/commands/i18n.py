"""Таржималар (web/i18n.py, web/i18n_tools.py):

* ``manage.py i18n update`` — коддаги матнларни йиғиб, locale/*.po ва .mo ни янгилайди
  (лотин — кирилл матндан ўзи транслитерация; ``# қўлда`` изоҳлиси сақланади);
* ``i18n compile`` — фақат .po → .mo;
* ``i18n check`` — таржимага ўралмаган кирилл матн ва русчаси йўқ матнлар (бўлса — хато билан чиқади);
* ``i18n todo [--limit N]`` — русчаси йўқ матнлар.
"""
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from web import i18n_tools as T
from web.translit import to_latin

MANUAL = "қўлда"  # лотин .po'да шу изоҳли таржима қайта ёзилмайди


def fill_latin(e):
    if MANUAL in " ".join(e.comments) and e.translated:
        return
    if e.msgid_plural is not None:
        e.msgstr_plural = {0: to_latin(e.msgid), 1: to_latin(e.msgid_plural)}
    else:
        e.msgstr = to_latin(e.msgid)
    e.flags.discard("fuzzy")


class Command(BaseCommand):
    help = "Таржималар: update | compile | check | todo | stats"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["update", "compile", "check", "todo", "stats"])
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--domain", default="")
        parser.add_argument("--path", default="", help="check/todo: фақат шу йўлдан бошланадиган файллар")

    def handle(self, action, limit, domain, path, **kw):
        if action == "update":
            for d in ("django", "djangojs"):
                n, _ = T.merge(d, "uz_Latn", fill_latin)
                n, left = T.merge(d, "ru")
                self.stdout.write(f"{d}: {n} та матн, русчаси йўқ: {left}")
        elif action == "compile":
            T.compile_all()
            self.stdout.write("tayyor")
        elif action == "todo":
            n = 0
            for d in ([domain] if domain else ["django", "djangojs"]):
                _, entries = T.read_po(T.ROOT / "locale" / "ru" / "LC_MESSAGES" / f"{d}.po")
                for e in entries:
                    if e.translated or (path and not any(r.startswith(path) for r in e.refs)):
                        continue
                    self.stdout.write(f"[{d}] {e.refs[0] if e.refs else ''}\n  {e.msgid!r}"
                                      + (f" / {e.msgid_plural!r}" if e.msgid_plural else ""))
                    n += 1
                    if limit and n >= limit:
                        return
        elif action == "stats":
            bad = T.leftovers()
            c = Counter()
            for p, v in bad.items():
                c[p] = len(v)
            for p, n in c.most_common():
                self.stdout.write(f"{n:5}  {p}")
            self.stdout.write(f"жами: {sum(c.values())}")
        else:
            bad = {p: v for p, v in T.leftovers().items() if p.startswith(path)}
            total = 0
            for p, items in bad.items():
                self.stdout.write(f"\n{p}")
                for ln, txt in items:
                    self.stdout.write(f"  {ln}: {txt}")
                total += len(items)
            missing = 0
            for d in ("django", "djangojs"):
                _, entries = T.read_po(T.ROOT / "locale" / "ru" / "LC_MESSAGES" / f"{d}.po")
                missing += sum(1 for e in entries if not e.translated and
                               (not path or any(r.startswith(path) for r in e.refs)))
            self.stdout.write(f"\nТаржимага ўралмаган: {total} та ({len(bad)} файл). Русчаси йўқ: {missing} та.")
            if total or missing:
                raise CommandError("таржима тўлиқ эмас")
