from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError

from .models import User, normalize_email

# Django валидаторлари хабарлари инглизча (USE_I18N = False) — смета­чига тушунарли тилда
PASSWORD_MESSAGES = {
    "password_too_short": "Пароль камида {min} белгидан иборат бўлсин.",
    "password_too_common": "Бу пароль жуда кенг тарқалган — бошқасини танланг.",
    "password_entirely_numeric": "Пароль фақат рақамлардан иборат бўлмасин.",
    "password_too_similar": "Пароль исм, фамилия ёки email'га жуда ўхшаш.",
}

INPUT = ("w-full rounded-lg border border-slate-300 px-3 py-2 text-sm "
         "focus:border-slate-900 focus:outline-none focus:ring-1 focus:ring-slate-900")


def password_errors(password, user) -> list:
    try:
        password_validation.validate_password(password, user)
    except ValidationError as e:
        out = []
        for err in e.error_list:
            text = PASSWORD_MESSAGES.get(err.code)
            out.append(text.format(min=(err.params or {}).get("min_length", "")) if text else err.messages[0])
        return out
    return []


def _attrs(**extra):
    return {"class": INPUT, **extra}


class LoginForm(forms.Form):
    email = forms.EmailField(label="Email", max_length=254,
                             widget=forms.EmailInput(attrs=_attrs(autocomplete="email", autofocus=True,
                                                                  placeholder="name@mail.uz")))
    password = forms.CharField(label="Пароль", max_length=128, strip=False,
                               widget=forms.PasswordInput(attrs=_attrs(autocomplete="current-password")))


class ResetRequestForm(forms.Form):
    email = forms.EmailField(label="Email", max_length=254,
                             widget=forms.EmailInput(attrs=_attrs(autocomplete="email", autofocus=True,
                                                                  placeholder="name@mail.uz")))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].error_messages.update(required="Email'ни киритинг.", invalid="Email нотўғри ёзилган.")


class SetPasswordForm(forms.Form):
    password1 = forms.CharField(label="Янги пароль", max_length=128, strip=False,
                                widget=forms.PasswordInput(attrs=_attrs(autocomplete="new-password", autofocus=True)))
    password2 = forms.CharField(label="Паролни такрорланг", max_length=128, strip=False,
                                widget=forms.PasswordInput(attrs=_attrs(autocomplete="new-password")))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.error_messages["required"] = "Тўлдиринг."

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Пароллар бир хил эмас.")
        elif p1:
            for msg in password_errors(p1, self.user):
                self.add_error("password1", msg)
        return data


class RegisterForm(forms.Form):
    first_name = forms.CharField(label="Исм", max_length=150,
                                 widget=forms.TextInput(attrs=_attrs(autocomplete="given-name", autofocus=True)))
    last_name = forms.CharField(label="Фамилия", max_length=150,
                                widget=forms.TextInput(attrs=_attrs(autocomplete="family-name")))
    email = forms.EmailField(label="Email", max_length=254,
                             widget=forms.EmailInput(attrs=_attrs(autocomplete="email", placeholder="name@mail.uz")))
    password1 = forms.CharField(label="Пароль", max_length=128, strip=False,
                                widget=forms.PasswordInput(attrs=_attrs(autocomplete="new-password")))
    password2 = forms.CharField(label="Паролни такрорланг", max_length=128, strip=False,
                                widget=forms.PasswordInput(attrs=_attrs(autocomplete="new-password")))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.error_messages["required"] = "Тўлдиринг."
        self.fields["email"].error_messages["invalid"] = "Email нотўғри ёзилган."

    def clean_email(self):
        email = normalize_email(self.cleaned_data["email"])
        if User.objects.filter(email=email).exists():
            raise ValidationError("Бу email билан ҳисоб бор — «Кириш»ни босинг.")
        return email

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Пароллар бир хил эмас.")
        elif p1:
            user = User(email=data.get("email", ""), first_name=data.get("first_name", ""),
                        last_name=data.get("last_name", ""))
            for msg in password_errors(p1, user):
                self.add_error("password1", msg)
        return data

    def save(self) -> User:
        d = self.cleaned_data
        return User.objects.create_user(d["email"], d["password1"], first_name=d["first_name"].strip(),
                                        last_name=d["last_name"].strip())
