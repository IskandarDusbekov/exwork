"""Server qayta yonganda (thread rejimi) to'xtab qolgan qayta hisob sahifa ochilganda qayta boshlanadi."""
import datetime as dt
from unittest import mock

import pytest
from django.utils import timezone

from jobs import recalc


@pytest.mark.django_db
def test_resume_starts_only_stale_or_free_pending_jobs(django_user_model):
    from jobs.models import Job
    user = django_user_model.objects.create_user(email="r@x.uz", password="x")
    now = timezone.now()
    dead = Job.objects.create(owner=user, recalc_seq=1, recalc_done=0, recalc_worker="thread:1",
                              recalc_heartbeat=now - dt.timedelta(minutes=30))
    free = Job.objects.create(owner=user, recalc_seq=2, recalc_done=1)
    alive = Job.objects.create(owner=user, recalc_seq=1, recalc_done=0, recalc_worker="thread:2", recalc_heartbeat=now)
    Job.objects.create(owner=user, recalc_seq=1, recalc_done=1)  # navbatda emas
    with mock.patch.object(recalc, "start") as start, mock.patch.object(recalc, "worker_mode", return_value=False):
        assert recalc.resume(Job.objects.all()) == 2
    assert {c.args[0] for c in start.call_args_list} == {dead.pk, free.pk}
    assert alive.pk not in {c.args[0] for c in start.call_args_list}
    with mock.patch.object(recalc, "start") as start, mock.patch.object(recalc, "worker_mode", return_value=True):
        assert recalc.resume(Job.objects.all()) == 0
    start.assert_not_called()
