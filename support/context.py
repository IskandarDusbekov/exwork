def support(request):
    """«Ёрдам» тугмасидаги белги: фойдаланувчи ҳали ўқимаган администратор жавоблари."""
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated) or getattr(request, "viewing_owner", None):
        return {}
    from .services import user_unread
    return {"support_unread": user_unread(user)}
