"""Ёрдам хизмати: хабар юбориш (чекловлар билан), ўқилди деб белгилаш, санаш ва хат билан хабарлаш."""
import logging
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from .models import Conversation, Message

log = logging.getLogger(__name__)

MAX_TEXT = 4000  # бир хабарда белги
MAX_FILE = 10 * 1024 * 1024  # 10 МБ
RATE_WINDOW = timedelta(minutes=10)
RATE_MAX = 20  # фойдаланувчи 10 дақиқада энг кўпи шунча хабар
RATE_GAP = 2  # хабарлар орасида камида шунча сония
EMAIL_GAP = timedelta(minutes=15)  # жавоб ҳақида хат — 15 дақиқада бир мартадан кўп эмас

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALLOWED_EXT = IMAGE_EXT | {".pdf", ".xls", ".xlsx", ".xlsm", ".doc", ".docx", ".txt", ".zip", ".rar"}
_MAGIC = {  # файл бошидаги байтлар: номи .png бўлган бошқа нарса расм сифатида кўрсатилмасин
    ".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"), ".webp": (b"RIFF",), ".pdf": (b"%PDF",),
    ".xlsx": (b"PK",), ".xlsm": (b"PK",), ".docx": (b"PK",), ".zip": (b"PK",), ".rar": (b"Rar!",),
    ".xls": (b"\xd0\xcf\x11\xe0",), ".doc": (b"\xd0\xcf\x11\xe0",),
}


class SupportError(Exception):
    """Фойдаланувчига кўрсатиладиган оддий тилдаги хато."""


def check_file(f) -> bool:
    """Тури ва ҳажми; ичи номига мос келиши. Қайтаради: расмми."""
    ext = Path(f.name or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise SupportError("Бу турдаги файл қабул қилинмайди. Расм (PNG, JPG), PDF, Excel, Word, TXT ёки ZIP юборинг.")
    if f.size > MAX_FILE:
        raise SupportError(f"Файл жуда катта ({f.size // 2**20} МБ). Энг кўпи — {MAX_FILE // 2**20} МБ.")
    if not f.size:
        raise SupportError("Файл бўш.")
    head = f.read(12)
    f.seek(0)
    sigs = _MAGIC.get(ext)
    if sigs and not any(head.startswith(s) for s in sigs):
        raise SupportError("Файлнинг ичи номига мос келмайди (масалан, .png деб номланган, лекин расм эмас).")
    if ext == ".webp" and head[8:12] != b"WEBP":
        raise SupportError("Файлнинг ичи номига мос келмайди.")
    return ext in IMAGE_EXT


def rate_wait(user) -> int:
    """Неча сония кутиш керак (0 — юбориш мумкин). Ходимлар чекланмайди."""
    if user.is_staff:
        return 0
    now = timezone.now()
    mine = Message.objects.filter(conversation__user=user, from_staff=False)
    last = mine.order_by("-created").values_list("created", flat=True).first()
    if last and (now - last).total_seconds() < RATE_GAP:
        return max(1, int(RATE_GAP - (now - last).total_seconds()))
    recent = list(mine.filter(created__gte=now - RATE_WINDOW).order_by("created").values_list("created", flat=True))
    if len(recent) >= RATE_MAX:
        return max(1, int((recent[0] + RATE_WINDOW - now).total_seconds()))
    return 0


def clean_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\x00", "").strip()
    if len(text) > MAX_TEXT:
        raise SupportError(f"Хабар жуда узун ({len(text)} белги). Энг кўпи — {MAX_TEXT} белги.")
    return text


def chat_of(user, create=True):
    if create:
        return Conversation.objects.get_or_create(user=user)[0]
    return Conversation.objects.filter(user=user).first()


@transaction.atomic
def post(conv: Conversation, author, text: str, upload=None, *, from_staff: bool) -> Message:
    """Хабар қўшади. Чекловлар (узунлик, файл) шу ерда; тезлик чеклови — view'да (фойдаланувчига)."""
    text = clean_text(text)
    is_image = check_file(upload) if upload else False
    if not text and not upload:
        raise SupportError("Хабар ёзинг ёки файл бириктиринг.")
    m = Message(conversation=conv, author=author, from_staff=from_staff, text=text, is_image=is_image)
    if upload:
        m.attachment_name = Path(upload.name).name[:150]
        m.attachment_size = upload.size
        m.attachment.save(upload.name, upload, save=False)
    m.save()
    conv.updated = m.created
    fields = ["updated"]
    if not from_staff and conv.status == Conversation.Status.CLOSED:  # фойдаланувчи яна ёзди — қайта очилади
        conv.status, conv.closed_at, conv.closed_by = Conversation.Status.OPEN, None, None
        fields += ["status", "closed_at", "closed_by"]
    conv.save(update_fields=fields)
    return m


def mark_read(conv: Conversation, *, by_staff: bool) -> int:
    """Иккинчи томон ёзган ўқилмаган хабарларни ўқилди деб белгилайди."""
    return conv.messages.filter(from_staff=not by_staff, read_at__isnull=True).update(read_at=timezone.now())


def user_unread(user) -> int:
    """Фойдаланувчи ҳали ўқимаган администратор жавоблари."""
    return Message.objects.filter(conversation__user=user, from_staff=True, read_at__isnull=True).count()


def staff_unread() -> int:
    """Администратор ҳали ўқимаган фойдаланувчи хабарлари (барча суҳбатлар)."""
    return Message.objects.filter(from_staff=False, read_at__isnull=True).count()


def as_json(m: Message, attach_url) -> dict:
    """Брузерга — фақат матн (HTML эмас): JS уни textContent билан чизади."""
    return {
        "id": m.pk, "staff": m.from_staff, "text": m.text,
        "time": timezone.localtime(m.created).strftime("%d.%m.%Y %H:%M"),
        "read": bool(m.read_at),
        "file": ({"name": m.attachment_name, "size": m.attachment_size, "image": m.is_image,
                  "url": attach_url(m)} if m.attachment else None),
    }


def notify_reply(request, conv: Conversation) -> bool:
    """Администратор жавоб берди — фойдаланувчига хат (email тасдиқланган ва почта созланган бўлса).
    Кетма-кет жавобларда хат тўлдириб юбормаймиз: 15 дақиқада бир марта. Хато асосий ишни бузмайди."""
    from django.core.cache import cache

    user = conv.user
    if not (user.email and user.email_verified and user.is_active):
        return False
    try:
        from panel import mail
        if not mail.configured():
            return False
    except Exception:  # noqa: BLE001
        return False
    key = f"support:mail:{conv.pk}"
    if not cache.add(key, 1, int(EMAIL_GAP.total_seconds())):
        return False
    try:
        from .emails import send_reply
        from django.urls import reverse
        send_reply(request.build_absolute_uri("/"), user, request.build_absolute_uri(reverse("support:page")))
        return True
    except Exception:  # noqa: BLE001
        log.exception("ёрдам хизмати: жавоб ҳақида хат юборилмади (%s)", user.email)
        cache.delete(key)
        return False
