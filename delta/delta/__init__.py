# delta runs a Celery worker + beat pair (recommendation similarity matrix
# recomputation); the compose entrypoint `celery -A delta ...` needs the app
# importable from the project package.
from .celery import app as celery_app

__all__ = ["celery_app"]
