# delta — deferred work

Things decided but deliberately not built yet. Newest first; delete an entry when
it ships.

## Profile & billing

- **Billing details in the profile form.** The "complete your profile" step ships
  with identity fields only (display name, nickname, address). Billing — invoice
  data, tax id, payment method — is a later block on the same form, and probably a
  separate model so an invoice can reference a frozen snapshot rather than the
  user's current address. Nothing in delta reads billing data today.
- **Where the extra fields live.** `toto.people.Person` (vendored toto-base) has
  `display_name/bio/avatar/date_of_birth/address(FK locations.Address)/email/phone`
  — no nickname, no billing. Delta should carry its own profile extension rather
  than migrate a vendored model; revisit if the fields turn out to be fleet-wide.

## Registration

- **E-mail activation.** `doc/plan/wymagania.tex` §Rejestracja requires activating
  an account by e-mail. Delta's `EMAIL_BACKEND` is `console` in both deploy
  configs, which is also why password reset self-disables. Needs real SMTP or
  `toto.jess` before activation can be more than a no-op.

## Auth follow-ups

- **zenobia's `email_verified` is weak.** `sso_master.services.get_user_claims`
  emits `email_verified = bool(user.email)` — i.e. "has an email at all", not
  "proved it owns it". Delta adopts an existing local account on a verified-email
  match (deliberate, chosen 2026-08), so for zenobia that adoption is only as
  strong as zenobia's own account hygiene. Tighten upstream: emit real
  verification state, then this stops being a caveat.
- **Socially-provisioned users get `password = ""`, not `set_unusable_password()`**
  (`social_login/views.py` `_resolve_user`). `has_usable_password()` therefore
  returns True for them, so Django's `PasswordResetForm.get_users()` includes
  accounts that have no password — contradicting the "reset only ever reaches a
  local account" property `sso_core/password_reset.py` documents. Small fix,
  belongs upstream in toto-auth.
- **Consumer-mode federation (`toto.sso_client`) as a later option.** Delta uses
  zenobia as a *social provider* instead (one button among many, chosen
  2026-08). If delta ever needs true parent–child federation — zenobia owning
  delta's accounts, roles flowing down — that is a different mode, and delta's
  vendored copy is the OLD pre-hardening one (matches on `email__iexact`, no
  `FederatedIdentity`, no pairing, no hybrid login page). It would have to be
  hand-copied forward from the portal's line first.
