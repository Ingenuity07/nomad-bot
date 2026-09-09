# Content Studio operations

## Production configuration

Set `DEBUG=False`, a secret-managed `DJANGO_SECRET_KEY`, explicit `ALLOWED_HOSTS`, and explicit `CORS_ALLOWED_ORIGINS`. Keep `CORS_ALLOW_ALL_ORIGINS=False`; enable secure session/CSRF cookies, HTTPS redirect, and HSTS only after HTTPS is confirmed end to end. `CONTENT_AUTOMATION_DEV_BOOTSTRAP` must be false.

Configure PostgreSQL with encrypted transport and encrypted volumes/backups. Configure Redis for Celery. Store `UPLOAD_POST_API_KEY`, `UPLOAD_POST_WEBHOOK_SECRET`, `ZERNIO_API_KEY`, and `ZERNIO_WEBHOOK_SECRET` in the deployment secret manager. Rotate one provider at a time, verify its internal health row, then remove the old secret. Do not place credentials in Django admin, logs, source control, or workspace settings.

Use private object storage for `SOCIAL_MEDIA_PRIVATE_STORAGE_*`. The publish storage may expose stable derivative URLs but must not allow directory listing or writes from the public internet. Prefer an empty `SOCIAL_MEDIA_EXTERNAL_URL_ALLOWLIST`.

Provider and queue bounds are documented in `env.example`. HTTP timeouts are restricted to 1–60 seconds, publish attempts to 1–5, and retry/claim windows are bounded at startup. The legacy publisher callback must set `LINKEDIN_LEGACY_CALLBACK_REQUIRE_TIMESTAMP=True` in production.

## Deploy and verify

1. Back up PostgreSQL and verify an object-storage recovery sample.
2. Run `python manage.py check --deploy` with production settings.
3. Run `python manage.py makemigrations --check --dry-run` and then `python manage.py migrate` once.
4. Deploy web workers, then Celery workers, then Celery Beat. Confirm all processes use UTC.
5. Confirm `/health/`, authenticated `/api/schema/`, and the staff-only `/api/internal/social/publishers/health/` response.
6. Exercise one internal workspace: connect, draft, approve exact version, schedule, publish, reconcile, metrics, disconnect, export, and deletion in a disposable workspace.

Celery Beat runs due publication every minute, reconciliation every five minutes, and metrics refresh hourly. Keep only one logical Beat scheduler. Multiple workers are safe because database leases and constraints protect each job; workers must share PostgreSQL.

## Monitoring and alerts

The internal health response contains `operations.status`, secret-free queue counts, and alert codes. Poll it from an authenticated internal monitor and alert on:

- any `STALE_CLAIMS` or sustained `OVERDUE_JOBS` for two checks;
- any sustained `UNRESOLVED_OUTCOMES` beyond the provider's normal acceptance time;
- `FAILURE_SPIKE` at the configured 24-hour threshold;
- `CONNECTIONS_NEED_ATTENTION` when scheduled posts are affected;
- provider configured/healthy becoming false;
- Celery task absence, PostgreSQL/Redis errors, webhook `401` spikes, webhook replay `409` spikes, storage failures, or media egress errors.

Batch tasks emit structured count objects; `social.audit` emits event/workspace/actor/target IDs and redacted detail. Route error logs to restricted retention. Never attach request headers, authorization URLs, full webhook bodies, generated copy, or provider responses to logs or alerts.

## Incident procedures

- Duplicate concern: stop Celery Beat, leave workers available for reconciliation, inspect job/attempt keys and provider status, and never create a replacement job until `UNKNOWN` is resolved.
- Callback failures: preserve request timestamps and delivery IDs outside application logs only in the provider's secure console; verify clock synchronization, the active secret, return URL, and signature scheme.
- Rate limits/outage: keep bounded retry settings, monitor `next_attempt_at`, and disable the provider for new work through admin if necessary. Existing jobs retain their snapshot and must be reconciled or explicitly cancelled; do not bulk rewrite providers.
- Connection revoked: scheduled jobs move to or remain `CONNECTION_REQUIRED`. Ask the customer to reconnect; do not substitute another social identity.
- Media exposure: revoke publish-object access, rotate storage credentials, preserve private originals, and regenerate derivatives after the cause is fixed.

## Time, export, and deletion

PostgreSQL stores aware UTC timestamps. Workspace IANA zones are validated. Schedule generation round-trips local wall time through UTC: nonexistent spring-forward times are skipped and the first occurrence of an ambiguous fall-back time is used. Test any newly supported timezone around both transitions.

Admin/owner export returns JSON content, versions, media metadata, connection display data, and redacted audit events; it omits vendor and opaque provider identifiers. Owner deletion blocks while a job is `PUBLISHING`, `SUBMITTED`, or `UNKNOWN`, then transactionally removes Content Studio/legacy LinkedIn data and schedules owned media deletion after commit. A minimal deletion audit receipt remains. Full user/workspace erasure remains an account-level operational process and must include backups according to the retention policy.

Review audit events for connection, approval, schedule, publish, provider-policy, export, and deletion changes. Test restoration quarterly and document backup retention and legal-hold exceptions outside the application repository.
