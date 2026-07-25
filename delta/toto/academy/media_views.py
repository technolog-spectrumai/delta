"""Enrollment-gated media serving for lessons.

Lesson video/notes live in the Vault (unencrypted). These views gate access to
the lesson's course — staff, the owning teacher, or a student enrolled in the
course — and stream video with HTTP Range support so ``<video>`` seeking works
in-browser and on mobile (spec F-05). PDF notes are served inline (browser
viewer → print).
"""

import mimetypes
import re

from django.contrib.auth.decorators import login_required
from django.http import (
    FileResponse,
    Http404,
    HttpResponseForbidden,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404

from .models import CourseEnrollment, Lesson

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_DEFAULT_TYPES = {
    "video": "video/mp4",
    "audio": "audio/mpeg",
    "pdf": "application/pdf",
}


def can_watch(user, course):
    """True if ``user`` may watch/download this course's lesson media."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_staff:
        return True
    person = getattr(user, "community_profile", None)
    if person is None:
        return False
    if course.owner_id and course.owner.person_id == person.id:
        return True
    return CourseEnrollment.objects.filter(course=course, student__person=person).exists()


def _content_type(vault_file):
    ctype, _ = mimetypes.guess_type(vault_file.title or "")
    return ctype or _DEFAULT_TYPES.get(vault_file.file_type, "application/octet-stream")


def _serve(request, vault_file, *, as_attachment=False):
    """Serve a VaultFile, honouring an HTTP Range request (206) when present."""
    file_field = vault_file.file
    content_type = _content_type(vault_file)
    size = file_field.size

    match = _RANGE_RE.match(request.META.get("HTTP_RANGE", "") or "")
    if not match:
        response = FileResponse(file_field.open("rb"), content_type=content_type)
        response["Accept-Ranges"] = "bytes"
        response["Content-Length"] = str(size)
        if as_attachment:
            response["Content-Disposition"] = f'attachment; filename="{vault_file.title}"'
        return response

    start = int(match.group(1)) if match.group(1) else 0
    end = int(match.group(2)) if match.group(2) else size - 1
    end = min(end, size - 1)
    start = min(start, end)
    length = end - start + 1

    fileobj = file_field.open("rb")
    fileobj.seek(start)

    def _chunks(remaining, chunk=8192):
        try:
            while remaining > 0:
                data = fileobj.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data
        finally:
            fileobj.close()

    response = StreamingHttpResponse(_chunks(length), status=206, content_type=content_type)
    response["Content-Range"] = f"bytes {start}-{end}/{size}"
    response["Accept-Ranges"] = "bytes"
    response["Content-Length"] = str(length)
    return response


def _gated_media(request, pk, attr, *, as_attachment=False):
    lesson = get_object_or_404(
        Lesson.objects.select_related("module__course", "module__course__owner", attr), pk=pk)
    vault_file = getattr(lesson, attr)
    if vault_file is None or vault_file.is_encrypted:
        raise Http404
    if not can_watch(request.user, lesson.module.course):
        return HttpResponseForbidden()
    return _serve(request, vault_file, as_attachment=as_attachment)


@login_required
def lesson_video(request, pk):
    """Stream a lesson's video (inline, range-capable) to entitled viewers."""
    return _gated_media(request, pk, "video_file")


@login_required
def lesson_notes(request, pk):
    """Serve a lesson's PDF notes inline (viewable / printable) to entitled viewers."""
    return _gated_media(request, pk, "notes_file")
