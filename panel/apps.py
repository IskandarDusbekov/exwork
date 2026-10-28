from django.apps import AppConfig


class PanelConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "panel"
    verbose_name = "Бошқарув панели"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed

        from . import audit, sessions
        from .models import Event

        def on_login(sender, request, user, **kw):
            audit.log(request, Event.Kind.LOGIN, actor=user)
            if request is not None and hasattr(request, "session"):
                sessions.start(request, user)

        def on_logout(sender, request, user, **kw):
            if user is not None:
                audit.log(request, Event.Kind.LOGOUT, actor=user)
            if request is not None and getattr(request, "session", None) is not None and request.session.session_key:
                sessions.end(request.session.session_key, "logout")

        def on_failed(sender, credentials, request=None, **kw):
            audit.log(request, Event.Kind.LOGIN_FAILED, email=credentials.get("username", ""))

        user_logged_in.connect(on_login, weak=False, dispatch_uid="panel_login")
        user_logged_out.connect(on_logout, weak=False, dispatch_uid="panel_logout")
        user_login_failed.connect(on_failed, weak=False, dispatch_uid="panel_login_failed")
