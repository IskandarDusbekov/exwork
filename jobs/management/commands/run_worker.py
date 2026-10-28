"""Fondagi ishchi: navbatdagi papkalarni ochadi va narxlaydi.

    python manage.py run_worker              # 2 ta oqim, to'xtatilguncha
    python manage.py run_worker --threads 4
    python manage.py run_worker --once       # navbat bo'shaguncha ishlaydi va chiqadi (cron / test uchun)

Bir nechta worker (bir nechta serverda ham) bir vaqtda ishlashi mumkin — har bir loyihani faqat bittasi oladi.
Sayt ``NARXLASH_TASKS=worker`` bilan ishga tushirilishi kerak (aks holda sayt o'zi oqimda ishlaydi).
Ctrl+C — yangi ish olinmaydi, boshlanganlari tugatiladi; ikkinchi Ctrl+C — darhol chiqish
(tugallanmagan ish 5 daqiqadan keyin boshqa worker tomonidan davom ettiriladi)."""
import os
import signal
import socket
import threading
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import F
from django.utils import timezone

from jobs import folders
from jobs.models import Worker


class Command(BaseCommand):
    help = "Fondagi ishchi: navbatdagi papkalarni ochadi va narxlaydi"

    def add_arguments(self, parser):
        parser.add_argument("--threads", type=int, default=2, help="bir vaqtda nechta loyiha (standart 2)")
        parser.add_argument("--poll", type=float, default=1.0, help="navbat bo'sh bo'lsa necha soniyada tekshiriladi")
        parser.add_argument("--once", action="store_true", help="navbat bo'shaguncha ishlab, chiqadi")

    def handle(self, *args, threads, poll, once, **opts):
        threads = max(1, min(threads, 16))
        self.name = f"{socket.gethostname()}:{os.getpid()}"
        self.stopping = threading.Event()
        self.idle = [False] * threads
        Worker.objects.update_or_create(name=self.name, defaults={
            "last_seen": timezone.now(), "threads": threads, "busy": 0, "done": 0})
        self._install_signals()
        self.stdout.write(f"Worker {self.name}: {threads} та оқим, навбат кутиляпти…")

        pool = [threading.Thread(target=self._loop, args=(i, poll, once), name=f"worker-{i}") for i in range(threads)]
        for t in pool:
            t.start()
        try:
            while any(t.is_alive() for t in pool):
                self._count(last_seen=timezone.now())
                close_old_connections()
                for t in pool:
                    t.join(timeout=5)
        finally:
            Worker.objects.filter(name=self.name).delete()
            self.stdout.write(f"Worker {self.name}: тўхтади")

    def _install_signals(self):
        def on_signal(signum, frame):
            if self.stopping.is_set():
                self.stderr.write("Дарҳол чиқиш")
                os._exit(1)
            self.stdout.write("Тўхтатиляпти: бошланган ишлар тугатилади (яна бир марта — дарҳол чиқиш)")
            self.stopping.set()
        signal.signal(signal.SIGINT, on_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, on_signal)

    def _loop(self, i, poll, once):
        me = f"{self.name}#{i}"
        try:
            while not self.stopping.is_set():
                try:
                    pid = folders.claim_next(me)
                except Exception as e:  # noqa: BLE001 — база вақтинча банд/узилган: оқим ўлмасин, кейинроқ
                    self.stderr.write(f"[{me}] навбатни олиб бўлмади: {type(e).__name__}: {e}")
                    close_old_connections()
                    self.stopping.wait(max(poll, 1.0))
                    continue
                if pid is None:
                    self.idle[i] = True
                    if once and all(self.idle):
                        return
                    self.stopping.wait(poll)
                    continue
                self.idle[i] = False
                self._count(busy=F("busy") + 1)
                started = time.monotonic()
                self.stdout.write(f"[{me}] {pid} — бошланди")
                try:
                    folders._run(pid, worker=me)
                except Exception as e:  # noqa: BLE001 — битта иш хатоси worker'ни тўхтатмасин
                    self.stderr.write(f"[{me}] {pid} — хато: {type(e).__name__}: {e}")
                finally:
                    self._count(busy=F("busy") - 1, done=F("done") + 1)
                self.stdout.write(f"[{me}] {pid} — тугади ({time.monotonic() - started:.1f} с)")
        finally:
            self.idle[i] = True  # чиққан оқим бошқаларнинг «--once» тугашига халал бермасин
            close_old_connections()

    def _count(self, **fields):
        """Панел учун ҳисоблагич — ёзилмаса ҳам иш тўхтамайди."""
        try:
            Worker.objects.filter(name=self.name).update(**fields)
        except Exception:  # noqa: BLE001
            close_old_connections()
