"""Small helpers shared by back-office module forms."""

from django.utils.text import slugify


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
