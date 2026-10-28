"""Gunicorn (production): ``gunicorn web.wsgi:application -c deploy/gunicorn.conf.py``.

Katta fayl yuklash va .xls o'girish so'rov ichida bir necha daqiqa olishi mumkin — timeout uzun.
Og'ir ish (papka ochish/narxlash) alohida ``run_worker`` jarayonida, bu yerda emas."""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8000")  # faqat nginx orqali — tashqaridan ochiq emas
workers = int(os.environ.get("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 5)))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", 2))
timeout = 300
graceful_timeout = 60
keepalive = 5
max_requests = 1000  # xotira asta-sekin o'sib ketmasin — jarayon vaqti-vaqti bilan yangilanadi
max_requests_jitter = 100
accesslog = "-"  # journalctl -u narxlash-web
errorlog = "-"
