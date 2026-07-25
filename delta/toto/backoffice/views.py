from .access import teacher_required
from .shell import backoffice_render


@teacher_required
def dashboard(request):
    """Back-office landing: a grid of authoring module cards."""
    return backoffice_render(request, "backoffice/dashboard.html", {}, active=None)
