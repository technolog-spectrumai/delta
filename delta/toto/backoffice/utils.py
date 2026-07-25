"""Small helpers shared by back-office module forms."""

from django.utils.text import slugify


def apply_reorder(items, request, *, id_param="item"):
    """Reorder ``items`` (each having an integer ``order``) from a POST request.

    Drag-and-drop posts a full ``order`` id list; the up/down buttons post a
    single ``<id_param>`` + ``direction``. Either way ``order`` is renumbered
    1..n. Returns True when a full-order list was applied (caller may 204).
    """
    items = list(items)
    order_ids = request.POST.getlist("order")
    bulk = bool(order_ids)
    if bulk:
        pos = {int(raw): n for n, raw in enumerate(order_ids) if raw.isdigit()}
        items.sort(key=lambda obj: pos.get(obj.id, len(items)))
    else:
        ids = [obj.id for obj in items]
        try:
            idx = ids.index(int(request.POST.get(id_param)))
        except (TypeError, ValueError):
            idx = None
        if idx is not None:
            swap = idx - 1 if request.POST.get("direction") == "up" else idx + 1
            if 0 <= swap < len(items):
                items[idx], items[swap] = items[swap], items[idx]
    for n, obj in enumerate(items, start=1):
        if obj.order != n:
            obj.order = n
            obj.save(update_fields=["order"])
    return bulk


def add_math_preview(field):
    """Tag a textarea field so mathpreview.js renders a live KaTeX preview under it."""
    css = field.widget.attrs.get("class", "")
    field.widget.attrs["class"] = (css + " js-mathsource").strip()


def unique_slug(model, base_text, *, instance=None, slug_field="slug", **scope):
    """A unique slug for ``model`` derived from ``base_text``.

    ``scope`` narrows the uniqueness check (e.g. ``group=<group>`` for a slug that
    is only unique per group). Excludes ``instance`` on edit.
    """
    base = slugify(base_text) or "item"
    slug = base
    i = 2
    while True:
        qs = model.objects.filter(**{slug_field: slug}, **scope)
        if instance is not None and instance.pk:
            qs = qs.exclude(pk=instance.pk)
        if not qs.exists():
            return slug
        slug = f"{base}-{i}"
        i += 1
