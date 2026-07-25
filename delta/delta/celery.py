"""Celery application for the delta host.

The deploy stack runs a dedicated worker and beat container, both invoked
as `celery -A delta ...` (see scripts/deploy.py, services.celery). Tasks
are discovered from the INSTALLED_APPS `tasks.py` modules; the schedule
lives in settings.CELERY_BEAT_SCHEDULE.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "delta.settings")

app = Celery("delta")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
