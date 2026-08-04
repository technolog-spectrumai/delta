"""Primula (Sheets) behind the teacher gate.

On delta the whole app is teachers-only: sheets are an authoring tool, like the
rest of the Panel autorski. Rather than forking the vendored app, every primula
route is re-mounted here with its callback wrapped in ``teacher_required`` —
the same existence-hiding gate the back office uses (login redirect for
anonymous, ``Http404`` for a signed-in non-teacher, staff always passes).

The namespace stays ``primula`` so the app's internal reverses and the vault's
"Open in Primula" editor plugin (``primula:edit``) keep working unchanged.
``urls.py`` mounts this module at ``/primula/`` instead of
``toto.primula.urls``; the wrapped patterns are copies, so the vendored module
itself is never mutated (its own test suite runs it ungated).
"""

from django.urls import path

from toto.backoffice.access import teacher_required
from toto.primula.urls import urlpatterns as _primula_patterns

app_name = "primula"

urlpatterns = [
    path(str(p.pattern), teacher_required(p.callback), name=p.name)
    for p in _primula_patterns
]
