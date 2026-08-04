"""The lesson presentation becomes a memo deck.

Every DB-backed deck (LessonPresentation + ordered PresentationSlide rows) is
converted into one self-contained ``.pml`` vault file — the format
``toto.memo`` edits and plays everywhere in the toto suite — and attached to
the lesson as ``presentation_file``. Then the two tables are dropped.

Conversion choices, and why:

* Slide bodies were Markdown (+ ``$LaTeX$``, + inline SVG/HTML) rendered by
  markdownx at view time. They are rendered ONCE here and stored as ``html``
  blocks — memo's v1 escape hatch, whose trust model is exactly what the old
  player's ``|safe`` already was. Rendering at conversion rather than keeping
  markdown means the file is self-contained and looks the same in memo as it
  did in the old player.
* The per-slide caption (``subtitle``) becomes a trailing emphasised line of
  the same block, which is where the old player drew it: under the content.
* The file's owner is the course owner's user (the teacher who owns the
  course), falling back to any staff user: a vault file must belong to
  someone, and the course owner is who edited these decks. It lands in that
  user's personal local bucket, the same place the panel's uploads go.
* A deck with no owner candidate at all is left in the database untouched —
  losing content in a migration is worse than keeping a dead table row — and
  reported.

One-way, by decision. Reversing re-creates empty tables and nothing else.
"""

import hashlib

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import migrations, models
from django.utils.text import slugify


def _markdown(text):
    """Markdown -> HTML the way the old player did it, degrading to <pre>."""
    try:
        from markdownx.utils import markdownify
        return markdownify(text or "")
    except Exception:                                      # noqa: BLE001
        import html
        return f"<pre>{html.escape(text or '')}</pre>" if text else ""


def _deck_xml(pres, slides):
    """The .pml bytes for one old deck."""
    from toto.memo import presentation_format as pf

    out = []
    for slide in slides:
        body = _markdown(slide.body)
        if slide.subtitle:
            body += f"<p><em>{_escape(slide.subtitle)}</em></p>"
        blocks = []
        if body:
            blocks.append(pf.Block(type="html", payload=body))
        out.append(pf.Slide(title=slide.title or "", layout="title-content",
                            blocks=blocks))
    deck = pf.Presentation(
        title=pres.title or pres.lesson.title,
        slides=out or [pf.Slide()],
    )
    return pf.dumps(deck).encode("utf-8")


def _escape(text):
    import html
    return html.escape(str(text))


def _owner_for(lesson, User):
    course = lesson.module.course
    teacher = getattr(course, "owner", None)
    person = getattr(teacher, "person", None)
    user = getattr(person, "user", None)
    if user:
        return user
    return User.objects.filter(is_staff=True).order_by("pk").first()


def _unique_key(VaultFile, bucket, base):
    key, n = base or "deck", 1
    while VaultFile.objects.filter(bucket=bucket, key=key).exists():
        n += 1
        key = f"{base}-{n}"
    return key


def convert(apps, schema_editor):
    LessonPresentation = apps.get_model("academy", "LessonPresentation")
    VaultFile = apps.get_model("vault", "VaultFile")
    Bucket = apps.get_model("vault", "Bucket")
    User = apps.get_model(settings.AUTH_USER_MODEL)

    stranded = []
    for pres in (LessonPresentation.objects
                 .select_related("lesson__module__course__owner__person")):
        lesson = pres.lesson
        slides = list(pres.slides.order_by("order"))
        if not slides:
            pres.delete()          # an empty deck carries nothing to keep
            continue
        owner = _owner_for(lesson, User)
        if owner is None:
            stranded.append(pres.pk)
            continue

        # The same personal local bucket the panel's uploads use.
        bucket, _ = Bucket.objects.get_or_create(
            slug=f"personal-{owner.username}",
            defaults={"name": f"{owner.username} personal",
                      "owner_id": owner.pk, "storage_backend": "local"})

        xml = _deck_xml(pres, slides)
        base = slugify(lesson.title or "lesson") or "lesson"
        name = f"{base}-deck.pml"
        vault_file = VaultFile(
            owner_id=owner.pk, title=name,
            key=_unique_key(VaultFile, bucket, f"{base}-deck"),
            file_type="presentation", bucket=bucket,
            is_public=False, is_encrypted=False)
        vault_file.file.save(name, ContentFile(xml), save=True)
        vault_file.content_hash = hashlib.sha256(xml).hexdigest()
        vault_file.file_size_bytes = len(xml)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])

        lesson.presentation_file_id = vault_file.pk
        lesson.save(update_fields=["presentation_file"])
        pres.delete()

    if stranded:
        # No owner candidate anywhere — a dev database with no staff user.
        # The rows stay; the DeleteModel below would lose them, so refuse.
        raise RuntimeError(
            f"cannot convert LessonPresentation rows {stranded}: no course "
            "owner and no staff user to own the vault file. Create one and "
            "re-run the migration.")


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0005_lessonpresentation_presentationslide"),
        ("vault", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="lesson",
            name="presentation_file",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="academy_lesson_presentations",
                to="vault.vaultfile",
                help_text="Optional slide-deck lecture for this lesson (a memo presentation from Vault).",
            ),
        ),
        migrations.RunPython(convert, migrations.RunPython.noop),
        migrations.DeleteModel(name="PresentationSlide"),
        migrations.DeleteModel(name="LessonPresentation"),
    ]
