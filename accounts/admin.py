from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import AuthAttempt, User


class UserCreation(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("email", "first_name", "last_name")


class UserChange(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChange
    add_form = UserCreation
    ordering = ("-date_joined",)
    list_display = ("email", "first_name", "last_name", "is_active", "is_staff", "date_joined", "last_login")
    list_filter = ("is_active", "is_staff", "is_superuser", "email_verified")
    search_fields = ("email", "first_name", "last_name")
    readonly_fields = ("date_joined", "last_login")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Шахсий", {"fields": ("first_name", "last_name", "email_verified")}),
        ("Ҳуқуқлар", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Саналар", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "first_name", "last_name", "password1", "password2")}),
    )


@admin.register(AuthAttempt)
class AuthAttemptAdmin(admin.ModelAdmin):
    list_display = ("created", "kind", "email", "ip")
    list_filter = ("kind",)
    search_fields = ("email", "ip")
