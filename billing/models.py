# i18n: ok-file — модел майдонлари номи (фақат Django admin ва панел)
"""Тўлов тизими: тариф, обуна, ҳисоб (баланс), дафтар, очилган (тўланган) файллар, тўловлар.

Пул — бутун сўмда (тийинсиз). Баланс фақат ``services.post`` орқали ўзгаради: ҳар ўзгариш дафтарда
(``Entry``) қолдиғи билан ёзилади — баланс ва дафтар доим мос."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Tariff(models.Model):
    """Ойлик тариф. Нархи 0 — бепул тариф (обунаси йўқ ҳамма шунда; биринчиси олинади)."""
    name = models.CharField("Номи", max_length=60)
    price = models.PositiveIntegerField("Нархи (сўм / ой)", default=0)
    rows = models.PositiveIntegerField("Қатор (ойига)", default=0)
    objects_limit = models.PositiveIntegerField("Техназор объекти (ойига)", default=0)
    note = models.CharField("Изоҳ (фойдаланувчига)", max_length=200, blank=True)
    active = models.BooleanField("Сотувда", default=True)  # ўчиқ — янги обуна йўқ, бор обуналар тугагунча ишлайди
    order = models.PositiveSmallIntegerField("Тартиб", default=0)
    # Тариф чегирмаси (акция): фоиз ва қачонгача (бўш — муддатсиз). Узоқ муддат чегирмаси билан кўпайтирилади.
    discount = models.PositiveSmallIntegerField("Чегирма, %", default=0)
    discount_until = models.DateField("Чегирма қачонгача", null=True, blank=True)
    details = models.TextField("Батафсил (фойдаланувчига)", blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "price", "id"]
        verbose_name = "Тариф"
        verbose_name_plural = "Тарифлар"

    def __str__(self):
        return self.name

    @property
    def free(self) -> bool:
        return self.price == 0

    @property
    def discount_now(self) -> int:
        """Ҳозир амалдаги чегирма (муддати ўтган бўлса — 0)."""
        if not self.discount or (self.discount_until and timezone.localdate() > self.discount_until):
            return 0
        return min(90, self.discount)


class Account(models.Model):
    """Фойдаланувчи ҳисоби — икки ҳамён, аралашмайди:
    * ``balance`` — ҲАҚИҚИЙ пул (Click орқали тўланган; қайтариш мумкин);
    * ``bonus`` — совға пул (админ бонуси; қайтарилмайди). Сарфлашда аввал бонус ишлатилади."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="billing")
    balance = models.BigIntegerField("Ҳақиқий пул (сўм)", default=0)
    bonus = models.BigIntegerField("Бонус (сўм)", default=0)
    auto_renew = models.BooleanField("Обуна ўзи янгилансин", default=True)
    # Арзонроқ тарифга ўтиш: ҳозиргиси тугагач шу тариф балансдан олинади (ҳозиргиси охиригача ишлайди)
    next_tariff = models.ForeignKey("Tariff", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                    verbose_name="Кейинги даврдан")
    next_months = models.PositiveSmallIntegerField("Кейинги давр, ой", default=1)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ҳисоб"
        verbose_name_plural = "Ҳисоблар"

    def __str__(self):
        return f"{self.user} · {self.balance} + {self.bonus}"

    @property
    def total(self) -> int:
        """Сарфлаш мумкин бўлган жами пул."""
        return self.balance + self.bonus


class Subscription(models.Model):
    """Пулли тариф даври. Чегаралар сотиб олинган пайтдагидек сақланади (тариф кейин ўзгарса ҳам)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions")
    tariff = models.ForeignKey(Tariff, on_delete=models.PROTECT, related_name="subscriptions")
    price = models.PositiveIntegerField("Тўланган (жами)", default=0)
    paid_bonus = models.PositiveIntegerField("Шундан бонусдан", default=0)
    months = models.PositiveSmallIntegerField("Муддат, ой", default=1)
    rows = models.PositiveIntegerField("Қатор (ойига)", default=0)
    objects_limit = models.PositiveIntegerField("Объект (ойига)", default=0)
    start = models.DateTimeField("Бошланди", db_index=True)
    end = models.DateTimeField("Тугайди", db_index=True)
    gift = models.BooleanField("Админ совғаси", default=False)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start"]
        verbose_name = "Обуна"
        verbose_name_plural = "Обуналар"

    def __str__(self):
        return f"{self.user} · {self.tariff} · {self.start:%d.%m.%Y}–{self.end:%d.%m.%Y}"

    @property
    def active(self) -> bool:
        return self.start <= timezone.now() < self.end


class Entry(models.Model):
    """Дафтар: ҳар бир кирим (+) ва чиқим (−). ``ref`` — бир амал икки марта ёзилмасин (тўлов, файл)."""

    class Kind(models.TextChoices):
        TOPUP = "topup", "Тўлов (баланс тўлдирилди)"
        BONUS = "bonus", "Бонус"
        ADMIN = "admin", "Админ тузатиши"
        SUBSCRIPTION = "subscription", "Тариф"
        ROWS = "rows", "Смета қаторлари"
        OBJECT = "object", "Техназор объекти"
        REFUND = "refund", "Қайтарилди"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ledger")
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    amount = models.BigIntegerField("Сумма")  # + кирим, − чиқим (жами: ҳақиқий + бонус)
    bonus = models.BigIntegerField("Шундан бонус", default=0)  # ``amount`` нинг бонус ҳамёнидаги қисми
    balance_after = models.BigIntegerField("Қолдиқ (ҳақиқий)")
    bonus_after = models.BigIntegerField("Қолдиқ (бонус)", default=0)
    note = models.CharField("Изоҳ", max_length=255, blank=True)
    ref = models.CharField(max_length=120, null=True, blank=True, unique=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")  # админ тузатишида — ким

    class Meta:
        ordering = ["-created", "-id"]
        verbose_name = "Дафтар ёзуви"
        verbose_name_plural = "Дафтар"

    def __str__(self):
        return f"{self.user} {self.amount:+} ({self.kind})"

    @property
    def real(self) -> int:
        """Ҳақиқий пул қисми."""
        return self.amount - self.bonus

    @property
    def total_after(self) -> int:
        return self.balance_after + self.bonus_after


class Unlock(models.Model):
    """Пули тўланган (очилган) файл: смета (``job:<id>``) ёки техназор объекти шу ойда (``obj:<id>:2026-11``).
    Бир марта очилгани қайта юклаб олинганда яна ечилмайди. ``quota_rows`` — тариф ҳисобидан,
    ``paid_rows`` / ``amount`` — балансдан."""

    class Kind(models.TextChoices):
        ROWS = "rows", "Смета"
        OBJECT = "object", "Техназор объекти"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="unlocks")
    key = models.CharField(max_length=120)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    title = models.CharField(max_length=255, blank=True)
    rows = models.PositiveIntegerField(default=0)
    quota_rows = models.PositiveIntegerField(default=0)
    quota_object = models.BooleanField(default=False)
    amount = models.PositiveIntegerField(default=0)
    free = models.BooleanField(default=False)  # пулли режим ўчиқ пайтида очилган (ҳисобга кирмайди)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created"]
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="billing_unlock_unique")]
        indexes = [models.Index(fields=["user", "created"])]
        verbose_name = "Очилган файл"
        verbose_name_plural = "Очилган файллар"

    def __str__(self):
        return f"{self.user} · {self.key}"


class Payment(models.Model):
    """Тўлов тизими орқали тўлов (Click, кейин Payme). Пул балансга фақат ``PAID`` бўлганда тушади."""

    class Status(models.TextChoices):
        CREATED = "created", "Яратилди"
        PREPARED = "prepared", "Текширилди"
        PAID = "paid", "Тўланди"
        CANCELLED = "cancelled", "Бекор қилинди"
        FAILED = "failed", "Хато"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payments")
    provider = models.CharField(max_length=12, default="click")
    amount = models.PositiveIntegerField("Сумма")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CREATED, db_index=True)
    provider_id = models.CharField(max_length=64, blank=True, db_index=True)  # click_trans_id
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=255, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created"]
        constraints = [models.UniqueConstraint(fields=["provider", "provider_id"], condition=~models.Q(provider_id=""),
                                               name="billing_payment_provider_id")]
        verbose_name = "Тўлов"
        verbose_name_plural = "Тўловлар"

    def __str__(self):
        return f"{self.user} · {self.amount} · {self.status}"
