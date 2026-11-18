"""Эълонлар: кимга нима кўринади, нимаси ўқилмаган, ойнада қайсиниси чиқади."""
from django.db.models import Q
from django.utils import timezone

from .models import Announcement, AnnouncementRead

BELL_LIMIT = 15


def visible():
    now = timezone.now()
    return (Announcement.objects.filter(published=True, published_at__lte=now)
            .filter(Q(ends_at=None) | Q(ends_at__gt=now)))


def for_user(user) -> dict:
    """{"items": охирги эълонлар (ҳар бирида .unread), "unread": сони, "modal": ойнада чиқадигани ёки None}.
    Фойдаланувчи рўйхатдан ўтишидан олдинги эълонлар ўқилган ҳисобланади (янги одамга эски табрик чиқмасин)."""
    items = list(visible()[:BELL_LIMIT])
    if not items:
        return {"items": [], "unread": 0, "modal": None}
    read = set(AnnouncementRead.objects.filter(user=user, announcement__in=items)
               .values_list("announcement_id", flat=True))
    joined = getattr(user, "date_joined", None)
    for a in items:
        a.unread = a.pk not in read and not (joined and a.published_at < joined)
    modal = next((a for a in items if a.unread and a.show_modal), None)
    return {"items": items, "unread": sum(a.unread for a in items), "modal": modal}


def mark_read(user, ids) -> int:
    ids = list(visible().filter(pk__in=ids).values_list("pk", flat=True))
    AnnouncementRead.objects.bulk_create([AnnouncementRead(announcement_id=i, user=user) for i in ids],
                                         ignore_conflicts=True)
    return len(ids)
