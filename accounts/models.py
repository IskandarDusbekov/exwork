from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


def normalize_email(email: str) -> str:
    """Email катта-кичик ҳарфсиз сақланади: Ali@Mail.uz ва ali@mail.uz — битта ҳисоб."""
    return (email or "").strip().lower()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        email = normalize_email(email)
        if not email:
            raise ValueError("Email керак")
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self._create_user(email, password, **extra)

    def get_by_natural_key(self, email):
        return self.get(email=normalize_email(email))


class User(AbstractBaseUser, PermissionsMixin):
    """Смета­чи: email билан киради (username йўқ). ``email_verified`` — кейин OTP билан тасдиқланади."""
    email = models.EmailField("Email", unique=True)
    first_name = models.CharField("Исм", max_length=150)
    last_name = models.CharField("Фамилия", max_length=150)
    email_verified = models.BooleanField("Email тасдиқланган", default=False)
    is_active = models.BooleanField("Фаол", default=True)
    is_staff = models.BooleanField("Админ панелга киради", default=False)
    date_joined = models.DateTimeField("Рўйхатдан ўтган", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = "Фойдаланувчи"
        verbose_name_plural = "Фойдаланувчилар"

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name


class AuthAttempt(models.Model):
    """Кириш/рўйхатдан ўтиш уринишлари — пароль танлаб топишга (brute force) қарши чеклов учун."""

    class Kind(models.TextChoices):
        LOGIN = "login", "Кириш (хато)"
        REGISTER = "register", "Рўйхатдан ўтиш"

    kind = models.CharField(max_length=16, choices=Kind.choices)
    email = models.CharField(max_length=254, blank=True, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["kind", "created"])]
