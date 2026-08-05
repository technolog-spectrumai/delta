"""Bring existing worked solutions up to cyprian's sanitiser policy.

The solutions were written in Trix and stored raw — no sanitisation ever ran on
them, and Trix attaches images by URL (``/media/...``). Cyprian's policy keeps
images only as ``data:`` URIs and drops every other ``<img>``, so the first time
a teacher opened an old solution and pressed Save, its pictures would vanish
silently.

Doing it here instead makes the change visible and reversible-by-restore: the
images are inlined as data URIs now, while the files are still on disk, and
anything already unreachable is replaced with a marker rather than deleted
without trace.

Reverse is a no-op: the sanitised HTML is valid input to the old field too.
"""
import base64
import mimetypes
import os
import re

from django.conf import settings
from django.db import migrations


_IMG = re.compile(r'<img\b[^>]*>', re.IGNORECASE)
_SRC = re.compile(r'\bsrc\s*=\s*(["\'])(.*?)\1', re.IGNORECASE | re.DOTALL)


def _local_path(src):
    """The file a /media/... src points at, or None if it is not one of ours."""
    media_url = (getattr(settings, "MEDIA_URL", "") or "/media/").rstrip("/") + "/"
    if not src.startswith(media_url):
        return None
    relative = src[len(media_url):].split("?", 1)[0].split("#", 1)[0]
    if not relative or ".." in relative:
        return None
    return os.path.join(str(getattr(settings, "MEDIA_ROOT", "")), *relative.split("/"))


def _inline_images(html):
    """Rewrite <img src="/media/..."> as data URIs; mark the ones that are gone."""

    def replace(match):
        tag = match.group(0)
        src_match = _SRC.search(tag)
        if not src_match:
            return tag
        src = src_match.group(2)
        if src.startswith("data:"):
            return tag
        path = _local_path(src)
        if not path or not os.path.isfile(path):
            # Nothing to inline. Say so in the content rather than letting the
            # sanitiser delete the tag silently on the next save.
            return '<p><em>[image unavailable: %s]</em></p>' % src
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return '<p><em>[image unavailable: %s]</em></p>' % src
        mime = mimetypes.guess_type(path)[0] or "image/png"
        data_uri = "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))
        return tag[:src_match.start(2)] + data_uri + tag[src_match.end(2):]

    return _IMG.sub(replace, html or "")


def sanitise(apps, schema_editor):
    from toto.cyprian.sanitize_html import sanitize_content

    QuizQuestion = apps.get_model("quizzes", "QuizQuestion")
    for question in QuizQuestion.objects.exclude(solution="").iterator():
        cleaned = sanitize_content(_inline_images(question.solution))
        if cleaned != question.solution:
            question.solution = cleaned
            question.save(update_fields=["solution"])


def noop(apps, schema_editor):
    """Sanitised HTML is valid for the old field too — nothing to undo."""


class Migration(migrations.Migration):

    dependencies = [
        ("quizzes", "0005_alter_quizquestion_solution"),
    ]

    operations = [
        migrations.RunPython(sanitise, noop),
    ]
