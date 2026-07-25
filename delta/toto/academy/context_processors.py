"""Academy template context processors."""

from .models import WelcomeCopy


def welcome_copy(request):
    """Expose the editable, bilingual welcome-page copy to every template.

    A single light SELECT; when the singleton row does not exist yet an unsaved
    instance is returned so the localized-property defaults still render.
    """
    return {"welcome_copy": WelcomeCopy.objects.first() or WelcomeCopy()}
