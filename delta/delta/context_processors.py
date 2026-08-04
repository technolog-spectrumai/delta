"""Delta's build flags, for templates that must not link to an absent app.

`{% url %}` is resolved at render time and raises `NoReverseMatch` when the app
is gone, so a nav link to an optional capability has to be wrapped in a real
condition — hiding it with CSS is not enough. toto-base ships its own
`build_flags` processor for the suite's flags; this one carries delta's.
"""
from django.conf import settings


_FLAGS = (
    "BUILD_AUTHORING",
    "BUILD_LIBRARY",
    "BUILD_SUBSCRIPTIONS",
    "BUILD_VOD",
    "BUILD_SOCIAL_LOGIN",
)


def build_flags(request):
    return {name: bool(getattr(settings, name, False)) for name in _FLAGS}
