# Full secession: delta is independent of the toto_libs repo

As of the `full_secession` branch, delta carries every line of toto code it
runs. The one package it installs (`toto-base`) lives in this repo as a
**git subtree** at `vendor/toto_libs`, and the whole pipeline — dev server,
clean-env gate, Docker build, rsync push — works from this repo alone. No
sibling `../toto_libs` checkout, no external tag, no git URL. The six education
apps (`toto.academy`, `toto.quizzes`, `toto.competence`, `toto.palimpsest`,
`toto.library`, `toto.subscriptions`) were already host-carried at `delta/toto/`
(revived from limbo) and were never in any wheel.

## What was done

- Created the `delta` branch in toto_libs at commit `d9c34a0d` (suite v1.7) —
  the same tip faros vendored — as delta's upstream line.
- `git subtree add --prefix=vendor/toto_libs ../toto_libs delta --squash` —
  imported that branch as a single squashed snapshot. toto_libs history does
  not enter delta history.
- Trimmed the vendored copy to what delta installs: deleted `limbo/` (the six
  education apps already live in this repo) and the eight packages delta does
  not install (`toto-flow`, `toto-works`, `toto-geo`, `toto-media`,
  `toto-chat`, `toto-ops`, `toto-ai`, `toto-graph`). They still exist upstream.
- Repointed the toolchain from the sibling checkout to the vendored tree:
  `delta/scripts/deploy.py` (`_toto_src()`, `build_fingerprint()`, rsync
  excludes), `delta/manage.py`, `delta/scripts/download_vendor.py`.
- Added the clean-env gate `scripts/clean_env_test.sh` and the deploy unit
  tests `delta/scripts/test_deploy.py` (ports of faros' with delta
  adaptations).
- Exempted `vendor/toto_libs/**` from git-LFS filters in `.gitattributes` —
  delta LFS-tracks `*.png/pdf/zip` with unanchored patterns, and files arriving
  via future subtree pulls must stay plain git blobs.

## How toto resolves now

In order of precedence:

1. `TOTO_SRC` env (or `toto_src:` in a deploy yaml) — an explicit checkout.
   **Set-but-missing means "explicitly disabled"**: the clean-env gate points
   it at a nonexistent dir to prove delta runs from the wheel alone.
2. `vendor/toto_libs` — the default everywhere when `TOTO_SRC` is unset.
3. The installed wheel — the Docker image never sees `vendor/` (dockerignored);
   it installs the wheel deploy.py stages into `dist/`, offline
   (`pip install --no-index --find-links`).

## Versioning without an external pin

`requirements.toto.txt` keeps its exact pin (`toto-base==1.7`), but it no
longer points at anything outside the repo: it must equal
`vendor/toto_libs/VERSION` and both change in the same commit. `deploy.py`
still refuses to build on any drift between pin, vendored VERSION, and the
built wheel. The git-tag / clean-tree gate of `toto.versioning.verify_checkout`
self-disables for the vendored tree (no `.git` under the prefix) — the VERSION
file is the single version truth now (the vendored snapshot is not on a
toto_libs release tag; `d9c34a0d` is ahead of `v1.7`).

## Pulling future toto work from toto_libs

Delta-related toto development happens on the `delta` branch of toto_libs.
To bring it in:

```bash
git subtree pull --prefix=vendor/toto_libs ../toto_libs delta --squash \
  -m "pull toto_libs delta-branch updates into vendor/toto_libs"
# without a local sibling clone, use the git URL of toto_libs instead of ../toto_libs
```

Then, if `vendor/toto_libs/VERSION` moved, bump the pin in
`requirements.toto.txt` in the same series and run `./deploy_local.sh`.

Notes:
- Each pull adds one squash commit + one merge commit. Do not rewrite them —
  their `git-subtree-*` trailers drive the next pull.
- The trim deletions persist across pulls. If upstream *modifies* a trimmed
  file you get a modify/delete conflict: resolve with `git rm` on those paths.
  If upstream re-adds things under `limbo/` or the eight removed packages,
  delete them again.
- **Branch bookkeeping:** toto_libs' `delta` and `faros` branches started at
  the same commit (`d9c34a0d`). A toto-base fix landed on one branch must be
  merged or cherry-picked to the other, or the two hosts' vendored `toto-base`
  trees silently diverge.
- Local fixes made under `vendor/toto_libs` can be sent upstream with
  `git subtree push --prefix=vendor/toto_libs <toto_libs-url> <branch>`.

## Known quirks

- The vendored `vendor/toto_libs/scripts/clean_env_check.sh` is upstream's
  nine-package suite gate; it cannot pass against the trimmed one-package
  tree. Use delta's own `scripts/clean_env_test.sh`.
- `pip wheel` uses build isolation, which needs network (or a warm pip cache)
  to fetch setuptools the first time — same as before the secession.
- The gate runs GIS-off (`BUILD_GEO=0`, plain sqlite, the locations
  `migrations_nogis` graph) — no GDAL is needed on the host. The
  `delta_server.yaml` profile still builds with `BUILD_GEO=1`.

## Follow-ups

- Possible later adoption of `toto-works` (memo) and `toto-media`
  (vod/transcription) for video lessons: resurrect the packages from the
  squash commit (`git checkout <squash-sha> -- packages/toto-works`) or via a
  subtree pull, then add them to `requirements.toto.txt`.
- `delta/scripts/reset.py` is an unadapted faros copy with dead legacy paths —
  replace or remove it when it is next needed.
