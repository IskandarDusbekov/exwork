"""Юклаб олиш тугмаси ёнидаги ёзув: «тарифдан», «2 444 сўм», «пул етмайди», «тўланган» (пулли режим ўчиқ — ҳеч нарса)."""
from django import template

from billing import gate

register = template.Library()


@register.simple_tag(takes_context=True)
def bill_hint(context, work):
    request = context.get("request")
    user = getattr(request, "user", None)
    if work is None or user is None or not user.is_authenticated or getattr(request, "viewing_owner", None):
        return {}
    h = gate.hint(user, work)
    return {**h, "text": gate.label(h)}
