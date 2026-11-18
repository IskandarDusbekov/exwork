def news(request):
    """Сарлавҳадаги қўнғироқча ва кирганда бир марта чиқадиган ойна (news/_bell.html, news/_modal.html)."""
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated) or getattr(request, "viewing_owner", None):
        return {}
    from .services import for_user
    return {"news": for_user(user)}
