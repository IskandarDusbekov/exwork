"""Viloyatlar va matndan viloyatni taxmin qilish (smeta sarlavhasi, obyekt nomi, papka nomlari)."""
import re

REGIONS = [
    ("toshkent_sh", "Тошкент шаҳри"),
    ("toshkent_v", "Тошкент вилояти"),
    ("andijon", "Андижон вилояти"),
    ("buxoro", "Бухоро вилояти"),
    ("jizzax", "Жиззах вилояти"),
    ("qashqadaryo", "Қашқадарё вилояти"),
    ("navoiy", "Навоий вилояти"),
    ("namangan", "Наманган вилояти"),
    ("samarqand", "Самарқанд вилояти"),
    ("surxondaryo", "Сурхондарё вилояти"),
    ("sirdaryo", "Сирдарё вилояти"),
    ("fargona", "Фарғона вилояти"),
    ("xorazm", "Хоразм вилояти"),
    ("qoraqalpogiston", "Қорақалпоғистон Республикаси"),
]
REGION_LABELS = dict(REGIONS)

# kalit so'zlar (kichik harfda; kirill o'zbek, rus va lotin yozuvida): viloyat nomi, markazi, yirik shahar va tumanlar
_KEYWORDS = {
    "toshkent_sh": ["тошкент шаҳ", "тошкент шах", "г. ташкент", "г.ташкент", "город ташкент", "toshkent shahri", "юнусобод", "юнусабад",
                    "чилонзор", "чиланзар", "мирзо улуғбек", "мирзо-улугбек", "яккасарой", "яккасарай", "шайхонтоҳур",
                    "шайхантахур", "олмазор", "алмазар", "учтепа", "яшнобод", "яшнабад", "миробод", "мирабад", "сергели",
                    "бектемир", "янгиҳаёт"],
    "toshkent_v": ["тошкент вилоят", "ташкентская обл", "ташкентской обл", "toshkent viloyat", "қибрай", "кибрай", "чирчиқ",
                   "чирчик", "chirchiq", "ангрен", "олмалиқ", "алмалык", "бекобод", "бекабад", "янгийўл", "янгиюл", "зангиота",
                   "паркент", "бўка", "бука", "юқоричирчиқ", "ўртачирчиқ", "охангарон", "ахангаран", "нурафшон"],
    "andijon": ["андижон", "андижан", "andijon", "асака", "хонобод"],
    "buxoro": ["бухоро", "бухара", "бухарск", "buxoro", "когон", "каган", "ғиждувон", "гиждуван"],
    "jizzax": ["жиззах", "джизак", "jizzax", "зомин", "галлаорол"],
    "qashqadaryo": ["қашқадарё", "кашкадар", "qashqadaryo", "қарши", "карши", "qarshi", "шаҳрисабз", "шахрисабз", "китоб", "ғузор"],
    "navoiy": ["навоий", "навои", "navoiy", "зарафшон", "учқудуқ", "кармана"],
    "namangan": ["наманган", "namangan", "чуст", "поп тумани", "косонсой", "чортоқ"],
    "samarqand": ["самарқанд", "самарканд", "samarqand", "ургут", "urgut", "каттақўрғон", "каттакурган", "пайариқ", "булунғур",
                  "жомбой", "пастдарғом", "оқдарё", "нуробод", "иштихон", "yangi-dargom", "даргом"],
    "surxondaryo": ["сурхондарё", "сурхандар", "surxondaryo", "термиз", "термез", "termiz", "денов", "денау", "шеробод", "жарқўрғон"],
    "sirdaryo": ["сирдарё", "сырдар", "sirdaryo", "гулистон", "гулистан", "янгиер", "ширин", "сардоба", "боёвут"],
    "fargona": ["фарғона", "фергана", "ферганск", "farg'ona", "fargona", "қўқон", "коканд", "марғилон", "маргилан", "қувасой",
                "кувасай", "риштон", "бешариқ"],
    "xorazm": ["хоразм", "хорезм", "xorazm", "урганч", "ургенч", "urganch", "хива", "xiva", "хонқа", "шовот"],
    "qoraqalpogiston": ["қорақалпоғ", "каракалпак", "qoraqalpog", "нукус", "nukus", "беруний", "тўрткўл", "турткуль",
                        "хўжайли", "ходжейли", "чимбой", "қўнғирот", "кунград", "мўйноқ", "муйнак", "шимбай", "shimbay"],
}
_GENERIC_TASHKENT = re.compile(r"\b(?:тошкент|ташкент|toshkent|tashkent)\b")


def guess_region(texts) -> tuple:
    """(viloyat kaliti yoki "", qaysi so'z bo'yicha) — eng ko'p uchragan viloyat. Topilmasa ("", "")."""
    text = " ".join(t for t in texts if t).lower().replace("ё", "е")
    scores, why = {}, {}
    for key, words in _KEYWORDS.items():
        for w in words:
            n = text.count(w.replace("ё", "е"))
            if n:
                scores[key] = scores.get(key, 0) + n * (2 if len(w) > 6 else 1)
                why.setdefault(key, w)
    if not scores and _GENERIC_TASHKENT.search(text):
        return "toshkent_sh", "тошкент"  # «Тошкент» вилоят/туман белгисисиз — одатда шаҳар
    if not scores:
        return "", ""
    best = max(scores, key=scores.get)
    return best, why[best]
