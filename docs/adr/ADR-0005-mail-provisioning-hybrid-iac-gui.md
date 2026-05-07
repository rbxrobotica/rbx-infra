# ADR-0005: Mail Provisioning Model — Hybrid IaC/GUI by Login Criterion

## Status

**Accepted** — 2026-05-07

## Context

The Mailcow stack on `lince` exposes domain, mailbox, and alias
configuration through two channels:

- **GUI** at `https://mail.rbxsystems.ch/admin` — interactive, rich,
  the path Mailcow itself recommends.
- **REST API** documented at https://mailcow.docs.apiary.io/ — covers
  domains, mailboxes, aliases, ACLs, and most administrative actions
  but **not cleanly** the per-user attributes that interactive humans
  expect to manage themselves (signatures, filters, 2FA enrolment,
  password resets initiated by the user).

A pure-GUI model is fast for one-off changes but produces zero
reproducibility: a rebuild of `lince` requires restoring backup
state, never re-applying configuration from git. That conflicts with
the RBX IaC doctrine ("infraestrutura RBX precisa sempre garantir IaC
para reconstruir/reconciliar") which was reaffirmed during the
mailcow-host relay codification (ADR-0004 sibling work,
commit `8c0f1fb`).

A pure-IaC model satisfies the doctrine but creates split-brain anyway
because per-user 2FA, signatures, and filters cannot be cleanly
codified through the API. Forcing them through the API would fight the
tool, not use it.

A hybrid was therefore required. The remaining question — and the
substance of this ADR — is **where the line falls**.

## Decision

RBX uses a **hybrid provisioning model** for the Mailcow stack with a
single, textual boundary criterion.

### Boundary rule

```
If a human logs into the mailbox with 2FA, it is GUI-owned.
Otherwise — if the identity exists for protocol, compliance,
reporting, automation, or service purposes and no one logs in
interactively — it is IaC-owned.
```

The rule is **based on login behaviour, not category labels**.
"Login" means: a human authenticates and reads/composes mail through
the Mailcow GUI, SOGo, IMAP, or SMTP submission. "2FA" is the marker
that identifies a real human owner; service accounts do not have 2FA.

### Concrete partition

**GUI-owned** (human mailboxes — managed manually through the Mailcow
admin GUI):

- Personal mailboxes belonging to operators, employees, or contractors
- Any mailbox where a human authenticates to read or send
- Per-user state: 2FA enrolment, password resets initiated by the
  user, signatures, filters, ACLs, OAuth tokens, personal preferences

**IaC-owned** (codified in the `mailcow-host` Ansible role,
inventoried declaratively, applied via the Mailcow REST API):

- All managed mail **domains** (`rbxsystems.ch`, `strategos.gr`)
- **Protocol aliases** required by RFC 2142 / RFC 5321:
  `postmaster@`, `abuse@`, `hostmaster@`, `webmaster@` (where applicable)
- **Compliance / reporting addresses**: `dmarc@` (DMARC `rua`/`ruf`),
  `tlsreports@` (TLSRPT `rua`)
- **Service mailboxes**: `noreply@`, automation senders, addresses
  used by RBX systems (CI, monitoring) to send transactional or
  alerting mail
- **Aliases required for reconstruction**: any alias whose existence
  is asserted elsewhere in the system (DNS records, application
  configuration, contracts) and whose absence after a rebuild would
  produce a visible breakage

### Tagging convention

Every IaC-owned object is tagged `iac` in Mailcow.

- If the Mailcow REST API supports tags on the relevant object type
  (mailboxes do; aliases support comments/notes), apply the tag at
  creation/update time as part of the role.
- If the API does not support tags on a given object type, document
  the IaC-ownership in a clearly-labelled comment/note field on the
  object, with the literal string `iac` as a substring.
- Tags / annotations are the at-a-glance signal in the GUI:
  **anything tagged `iac` must not be edited through the GUI**.
  Operator edits to `iac`-tagged objects will be reverted on the next
  reconciliation.

### Reconciliation contract — drift detection with conservative auto-correction (Phase-1)

The `mailcow-host` role tasks that operate on Mailcow API objects
(domains, mailboxes, aliases) **must implement drift detection with
conservative auto-correction** in Phase-1:

1. **Always detect and report drift.** Read current state from the
   API, compare against the declarative inventory in
   `bootstrap/ansible/group_vars/mail_servers.yml` (or equivalent
   structured location), and surface the diff. Ansible `--check
   --diff` must produce meaningful output, not "ok=N changed=0" that
   hides mismatches.

2. **Create missing objects on apply.** Objects present in inventory
   but absent in Mailcow are created.

3. **Auto-correct only safe-to-reconcile attributes.** Each object
   type's reconciler defines an explicit allowlist of attributes
   that can be re-applied without operational risk. Attributes
   outside the allowlist are reported as drift but **not
   auto-corrected** — a human resolves them through PR or GUI as
   appropriate. The allowlist starts conservative and expands by
   ADR amendment as patterns prove safe in operation.

4. **Orphan delete remains opt-in / manual.** Objects in Mailcow
   tagged `iac` but absent from inventory are flagged for human
   review on apply, never auto-deleted by the role.

A task that only runs `POST /add` if the object is missing is **not
acceptable** under this ADR. The role contract for Phase-1 is:
"drift on any `iac`-tagged object is visible; safe-to-reconcile
drift is auto-corrected; everything else requires a human."

### Service-mailbox migration rule

A service mailbox **must not silently transition to a human mailbox**.

If circumstances change such that a previously-IaC-owned address
needs human login (e.g., `support@` evolves from a forwarding alias
into a shared inbox a team reads):

1. Open a PR that **removes the address from the IaC inventory**.
2. Apply the role — orphan flag fires, operator confirms intent.
3. Operator removes the `iac` tag in the GUI and provisions human
   attributes (password, 2FA, signature) interactively.

The migration is a deliberate act with a paper trail, never a side
effect of someone "just enabling 2FA on the noreply box".

### Shared mailbox rule

A mailbox that **multiple humans read or send from** is GUI-owned by
default, even if it has a "service-sounding" name. The boundary is
login behaviour: as soon as a human authenticates, the box is
GUI-owned.

Examples:

- `support@rbxsystems.ch` read by 3 people via SOGo → GUI-owned
- `noreply@rbxsystems.ch` sends transactional mail, no inbox read →
  IaC-owned
- `dmarc@rbxsystems.ch` receives DMARC reports parsed by automation,
  no human reads it → IaC-owned

If a human starts reading a previously-automation-only inbox, follow
the service-mailbox migration rule.

### Mailcow API token — secrets handling

The Mailcow REST API requires an admin token. This token:

- **Lives in `pass`** at the path `rbx/mailcow/api-token`.
- Is rendered into Ansible vault by `bootstrap/scripts/init-vault-from-pass.sh`
  as a top-level variable (suggested: `mailcow_api_token`), following
  the existing pattern for `postmark_server_token`,
  `paradedb_robson_password`, etc. (`docs/infra/SECRETS.md`).
- Has scope sufficient to manage domains, mailboxes, and aliases;
  read-write. Per-token scoping inside Mailcow is coarse — a single
  admin token is acceptable in Phase-1.
- **Is rotated** when:
  - A previous holder of admin access leaves the team
  - The token is suspected leaked (transcript exposure, accidental
    commit, etc.)
  - Annually as routine hygiene (calendar-driven)

### Token rotation runbook

Rotation procedure (to be added to `docs/runbooks/` when the role is
implemented; sketched here so the requirement is captured):

```bash
# 1. Generate a new admin token in the Mailcow GUI:
#    System → API → "Generate new token" — copy the value
# 2. Store it in pass (overwrite previous):
pass insert -f rbx/mailcow/api-token
# 3. Regenerate vault.yml:
bash bootstrap/scripts/init-vault-from-pass.sh
# 4. Apply the role to verify the new token works:
ansible-playbook -i bootstrap/ansible/inventory/hosts.yml --limit lince \
  bootstrap/ansible/site.yml --tags mail-provisioning --check
# 5. Revoke the old token in the Mailcow GUI:
#    System → API → previous token → revoke
```

The rotation must complete in this order: new token created and
verified before old token revoked. Rotating in the other order leaves
the role unable to authenticate during the window.

## Rationale

### Why a login-based criterion, not a category label

Category labels — "service mailbox", "human mailbox", "shared
mailbox" — drift with usage. The same address can move between
categories without any structural change. A category-based criterion
forces the operator to make subjective judgements at every edit:
"is `support@` a service or a shared box this week?"

A login-based criterion is observable. Either there is 2FA enrolment
on the account or there is not. Either someone authenticates in the
last 30 days of audit logs or no one does. The criterion can be
**checked**, not argued.

### Why tag-based, not naming-convention-based

A naming convention ("anything ending in `-svc`") would be a parallel
criterion that can drift from reality. The `iac` tag is part of the
object's actual state in Mailcow and visible in the GUI. The
operator cannot accidentally edit an `iac`-tagged box without seeing
the tag.

### Why drift detection, not idempotent create

Idempotent create — "POST if missing" — produces the desirable shape
in `--check` mode (`changed=0` after first run) without actually
reconciling. The mailcow-host relay codification (commit `8c0f1fb`)
demonstrated true reconciliation: the `extra.cf` template render was
**compared** to the on-disk file, and a one-character comment change
showed up as `changed: [lince]`. This ADR requires the same standard
for API-managed objects.

The cost is higher implementation effort: each task type (domain,
mailbox, alias) needs read/diff/create/update logic plus an
explicit allowlist of safe-to-reconcile attributes. The benefit is
real visibility: a manual GUI edit to an `iac`-tagged object is
detected on apply, with the safe-to-reconcile subset corrected
automatically and the rest surfaced as drift for human resolution.

### Why orphan auto-delete is opt-in for Phase-1

If the role auto-deletes `iac`-tagged objects that disappeared from
inventory, a misedit of `group_vars/mail_servers.yml` that drops
`postmaster@` deletes a mailbox with content. That is too sharp an
edge for a small operator team in Phase-1. Auto-delete becomes
appropriate when:

- The team is large enough that PR review is structurally enforced
- Restore drills are running cleanly (per ADR-0004) so deletion
  recovery is rehearsed

Until then, orphan flag + manual confirm is the right balance.

### Why 2FA, not "any login" as the human marker

Some service accounts authenticate over IMAP / SMTP submission for
their automation function. Plain authentication is not a reliable
human-marker. **2FA enrolment** is: humans enrol 2FA, automation
does not. This makes the criterion both observable and unambiguous.

If a service account ever needs 2FA (rare, but possible — e.g.,
shared-credential workflows for an audit boundary), the
service-mailbox migration rule applies before 2FA is enabled.

## Consequences

### Positive

- Reproducibility where it matters: every protocol-required and
  service identity can be re-created from git after a `lince` rebuild.
- Mailcow GUI strengths preserved for human mailboxes — no fighting
  the tool over per-user concerns it is designed to manage.
- Clear, testable boundary rule. Disagreement is resolvable by
  observation, not opinion.
- True drift detection means a manual GUI edit to an `iac` object
  cannot silently survive — the doctrine produces actual outcomes.
- Audit trail: protocol aliases live in git PRs; human mailboxes live
  in Mailcow audit logs. Two clean audit surfaces, not one muddy one.

### Negative

- Two mental models running in parallel — operators must internalise
  the boundary rule and the tag convention.
- Higher initial implementation cost than idempotent-create: real
  diffing requires per-object-type read/compare/update logic.
- Edge cases need explicit handling: shared mailbox lifecycle, service
  → human migration, orphan flagging.
- Mailcow upgrades may break the API contract, requiring the role to
  be updated in lockstep. Manageable, but a recurring tax.

### When to revisit

Revisit this ADR when any of these conditions hold:

- The team grows beyond two operators with admin access — boundary
  enforcement may need stricter tooling.
- Mailcow's API surface materially changes (e.g., adds first-class
  declarative configuration import/export).
- A human starts logging into a mailbox the role believes is
  service-only, and the migration rule is violated. This is an
  operational incident requiring root-cause review and likely a
  doctrine refresh.
- Regulatory / customer audit surfaces a need stricter than "iac
  tag + drift detection" — e.g., signed reconciliation, immutable
  audit trail.

## Related

- ADR-0004 — Mail Backup Target (the other half of the mail-stack IaC
  story; backup state and provisioning state interact during DR
  restore)
- `docs/PLAN-mail-self-hosted.md` — Mailcow architecture reference
- `docs/runbooks/MAIL-IMPLEMENTATION-STATUS.md` — current operational
  state; will gain a "Provisioning runbook" subsection when the role
  is implemented
- `docs/infra/SECRETS.md` — pass namespace conventions; will receive
  the `rbx/mailcow/api-token` entry when the role is implemented
- commit `8c0f1fb` — relay codification (precedent for true
  reconciliation pattern in mailcow-host role)
