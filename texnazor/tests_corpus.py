"""Haqiqiy ofyortalar to'plami — o'qish qoidalari o'zgarganda hammasi hali ham to'g'ri o'qilishi.
Fayllar repoda yo'q (mijoz ma'lumoti): ``TEXNAZOR_CORPUS=<papka>`` berilsa ishlaydi, aks holda o'tkazib yuboriladi.
``TEXNAZOR_CORPUS_IGNORE=Обмер,…`` — ofyorta bo'lmagan fayllar."""
import os
import unittest
from pathlib import Path

from django.test import SimpleTestCase

from .corpus import check_file, files

CORPUS = os.environ.get("TEXNAZOR_CORPUS", "")


@unittest.skipUnless(CORPUS and Path(CORPUS).is_dir(), "TEXNAZOR_CORPUS берилмаган")
class CorpusTests(SimpleTestCase):
    def test_every_ofyorta_passes_self_check(self):
        paths = files(Path(CORPUS), os.environ.get("TEXNAZOR_CORPUS_IGNORE", "").split(","))
        self.assertTrue(paths)
        for p in paths:
            with self.subTest(file=p.name):
                r = check_file(p)
                self.assertFalse(r["error"], r["error"])
                self.assertTrue(r["ok"], "; ".join(w["title"] for w in r["fails"]))
