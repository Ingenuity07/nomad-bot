# Content Studio rollout and rollback checklist

## Stage 1 — internal demo workspaces

- [ ] Production baseline and secret manager configuration reviewed by two people.
- [ ] Migrations, full backend/frontend tests, lint, production build, and `check --deploy` pass.
- [ ] One disposable workspace completes connection, onboarding, creation, exact-version approval, scheduling, publishing, webhook/poll reconciliation, analytics, export, disconnect, and deletion.
- [ ] LinkedIn compatibility endpoints continue to read/write the same workspace data.
- [ ] Keyboard-only approval/calendar fallback, visible focus, screen-reader headings/status, 320px mobile, tablet, and desktop layouts are checked.
- [ ] Alerts and on-call links are tested with synthetic overdue, stale, failure, and connection-required signals.
- [ ] No customer response, UI, export, or ordinary application log exposes a provider vendor or credential.

Exit only after at least one week without duplicate publication, cross-workspace access, unresolved outcomes beyond the agreed window, or high-severity accessibility defects.

## Stage 2 — small paid pilot

- [ ] Select a few customers with explicit support contacts, varied timezones, and LinkedIn Company Pages.
- [ ] Record consent, expected posting windows, provider capacity, and a manual stop procedure.
- [ ] Start with approval required and conservative weekly volume.
- [ ] Review queue health, webhook errors, rate limits, publish confirmation latency, media failures, reconnect rate, support load, and accessibility feedback daily.
- [ ] Validate exports and deletion in a pilot test workspace, not a live customer workspace.
- [ ] Compare published results with approved immutable versions and verify vendor names remain hidden.

Exit after two stable posting cycles and agreed error/support thresholds. Treat metrics as observational; do not infer causation from pilot correlations.

## Stage 3 — broader availability

- [ ] Publish support, privacy/retention, deletion, status, and incident communication procedures.
- [ ] Confirm provider capacity and rate-limit headroom for projected load.
- [ ] Confirm backup restore, secret rotation, dependency patching, and quarterly access review owners.
- [ ] Expand gradually by workspace cohort with an immediate cohort stop switch.
- [ ] Keep legacy LinkedIn endpoints until usage telemetry and customer migration criteria permit a separate removal plan.

## Rollback plan

1. Stop new onboarding/publishing by turning Content Studio scheduling off for affected workspaces or pausing Celery Beat. Disable a provider only for new routing if the incident is provider-specific.
2. Keep reconciliation available for `SUBMITTED` and `UNKNOWN` jobs. Never retry an unknown outcome manually before status lookup.
3. Roll the web/worker release back to the last compatible artifact. Migrations are additive; leave them applied during application rollback unless a tested restore requires otherwise.
4. Preserve PostgreSQL and object storage. If data corruption occurred, stop writes, snapshot evidence, restore into an isolated environment, validate tenant counts/checksums, then perform the approved restore.
5. Re-enable internal workspaces first, observe a complete publish/reconcile cycle, then re-enable pilot and broader cohorts.

Provider-routing invariant: changing `SOCIAL_PUBLISHER_DEFAULT`, a workspace override, or a provider enabled flag affects selection for new connections and new jobs only. Every connection records its creation provider and every `PublishJob` snapshots its provider. Existing scheduled jobs are never silently moved; tests in `test_publishing_routing.py` enforce this behavior.
