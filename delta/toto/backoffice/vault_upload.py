"""Create a ``vault.VaultFile`` from an uploaded file.

The one reusable "UploadedFile -> VaultFile" helper (vault itself only inlines
this in its views). Self-contained: it does not import vault's private helpers.
Files are stored **unencrypted** (``is_encrypted=False``) so the VOD player can
serve them, and default to non-public (served through an access-checked view).
"""

import mimetypes
import os

from django.utils.text import slugify


def _default_bucket(owner):
    """A per-user local (filesystem) bucket, created on first use."""
    from toto.vault.models import Bucket

    slug = f"personal-{owner.username}"
    bucket, _ = Bucket.objects.get_or_create(
        slug=slug,
        defaults={
            "name": f"{owner.username} personal",
            "owner": owner,
            "storage_backend": "local",
        },
    )
    return bucket


def _unique_key(base, bucket):
    from toto.vault.models import VaultFile

    base = base or "file"
    key, i = base, 2
    while VaultFile.objects.filter(bucket=bucket, key=key).exists():
        key = f"{base}-{i}"
        i += 1
    return key


def create_vault_file(owner, uploaded_file, *, is_public=False, file_type=None,
                      bucket=None, directory=None):
    """Persist ``uploaded_file`` as a plaintext VaultFile owned by ``owner``.

    ``file_type`` is auto-detected (video/pdf/…) from the filename/mime when not
    given. Returns the saved VaultFile.
    """
    from toto.vault.models import VaultFile

    if bucket is None:
        bucket = _default_bucket(owner)
    mime, _ = mimetypes.guess_type(uploaded_file.name)
    resolved_type = file_type or VaultFile.detect_type(mime or "", uploaded_file.name)
    base = slugify(os.path.splitext(os.path.basename(uploaded_file.name))[0])

    vault_file = VaultFile(
        owner=owner,
        title=os.path.basename(uploaded_file.name),
        key=_unique_key(base, bucket),
        file=uploaded_file,
        file_type=resolved_type,
        bucket=bucket,
        directory=directory,
        is_public=is_public,
        is_encrypted=False,
    )
    vault_file.save()
    vault_file.content_hash = vault_file.create_hash()
    vault_file.save(update_fields=["content_hash"])
    return vault_file
