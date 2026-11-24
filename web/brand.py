"""Юклаб олинадиган тайёр файл: номига « (mhisobot.uz)» (панел «Созламалар» → «Тайёр файллар» калити билан),
хотирада ясалган Excel'га файл хусусиятлари (доим). Қоидалар — ``core.brand``."""
from core import brand


def on() -> bool:
    from panel import conf
    return bool(conf.get("brand"))


def name(filename: str) -> str:
    return brand.file_name(filename) if on() else filename


def xlsx(data: bytes) -> bytes:
    return brand.stamp_bytes(data)
