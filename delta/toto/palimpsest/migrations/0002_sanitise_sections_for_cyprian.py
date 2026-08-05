"""Bring existing note sections up to cyprian's sanitiser policy.

Same reasoning as ``quizzes.0006``: section bodies were written in Trix and
stored raw — never sanitised, with images attached by ``/media/...`` URL.
Cyprian keeps images only as ``data:`` URIs and drops every other ``<img>``, so
this inlines them now, while the files are still on disk, instead of letting a
teacher's first Save delete them silently.

The helper is duplicated from the quizzes migration rather than imported from
one: a migration must keep working when the app code it was written against has
moved on, so it may not depend on today's modules.

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
    media_url = (getattr(settings, "MEDIA_URL", "") or "/media/").rstrip("/") + "/"
    if not src.startswith(media_url):
        return None
    relative = src[len(media_url):].split("?", 1)[0].split("#", 1)[0]
    if not relative or ".." in relative:
        return None
    return os.path.join(str(getattr(settings, "MEDIA_ROOT", "")), *relative.split("/"))


def _inline_images(html):
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

    Section = apps.get_model("palimpsest", "Section")
    for section in Section.objects.exclude(content="").iterator():
        cleaned = sanitize_content(_inline_images(section.content))
        if cleaned != section.content:
            section.content = cleaned
            section.save(update_fields=["content"])


def noop(apps, schema_editor):
    """Sanitised HTML is valid for the old field too — nothing to undo."""


class Migration(migrations.Migration):

    dependencies = [
        ("palimpsest", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(sanitise, noop),
    ]
