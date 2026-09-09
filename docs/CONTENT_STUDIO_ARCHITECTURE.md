# Content Studio architecture

Content Studio is the platform-neutral successor to LinkedIn Studio. Django and PostgreSQL own content, approval, scheduling, and publish state. Celery only claims due database jobs and asks the provider recorded on each job to publish them.

## Trust and tenancy boundary

- Customer APIs use Django session or Basic authentication. An unauthenticated request receives `401` unless the explicitly enabled local/test bootstrap is active.
- `WorkspaceMembership` supplies owner, admin, and member roles. Its active membership is used by default; `X-Workspace-ID` may select only another workspace the user belongs to. An inaccessible workspace receives `403`.
- API view querysets always begin with the resolved workspace. Posts, variants, versions, media, connections, onboarding, sources, metrics, and analytics are reached through that workspace.
- Export requires owner or admin. Content Studio deletion requires the owner and the exact phrase `DELETE CONTENT STUDIO`.
- `CONTENT_AUTOMATION_DEV_BOOTSTRAP` is effective only in debug or tests and defaults off in the example configuration.

Provider API keys and webhook secrets live in environment-backed deployment secrets and are never stored in social models. Provider profile, account, post, and event IDs are opaque identifiers, not credentials. They are restricted to backend/provider-adapter paths, omitted from customer connection responses and exports, and must be protected by database, backup, and staff-access controls. Logs and `SocialAuditEvent` details redact credential-like fields. Raw signed webhook bodies are never stored.

## Domain and lifecycle

`SocialPost` holds the shared idea. A `SocialPostVariant` holds platform copy, hashtags, schedule, connection, and ordered `MediaAsset` rows. Approval creates an immutable `SocialPostVersion`; subsequent copy, hashtag, schedule-sensitive media, or attachment edits revoke approval and require a new version.

The publish state machine is:

`DRAFT → NEEDS_REVIEW → APPROVED → SCHEDULED → PUBLISHING → SUBMITTED → PUBLISHED`

Terminal or intervention states are `FAILED`, `CANCELLED`, and `CONNECTION_REQUIRED`; `UNKNOWN` is an internal job outcome that must be reconciled before retry. A unique logical job key plus unique per-attempt keys prevent duplicate submissions. Due jobs are atomically leased, and stale leases are reconciled.

## Provider boundary and routing invariant

`PublishingProvider` owns HTTP translation only: connection URL/completion, accounts, validation, publish/cancel/status, webhook verification, metrics, and health. Adapters never write Django models. Application services own transactions and state changes.

The system default and administrator-only workspace override select a provider for **new** connections and jobs. `SocialConnection.provider` records where the account was connected. `PublishJob.provider` is a routing snapshot copied when the job is created. Execution and reconciliation use the job snapshot. Therefore changing `SOCIAL_PUBLISHER_DEFAULT` or a workspace override cannot move an existing connection or scheduled job.

Capabilities normalize networks, media types, cancellation, status lookup, webhooks, and metrics. Customer responses expose social-network capability and health, not vendor names.

## Media and network safety

Original uploads are private; stable publish derivatives use the configured publish storage. New database rows retain metadata and storage keys, not large binaries. File signatures, size, dimensions, duration, counts, compatible combinations, and alt text are checked before approval and before publishing.

Provider publishing accepts only owned derivative URLs or an explicit hostname allowlist. URL validation rejects userinfo, non-HTTP schemes, loopback/private/link-local/reserved IPs, and unexpected local paths. The legacy signed LinkedIn image endpoint remains available during migration.

## Callbacks and resilience

- OAuth state is random, stored only as a one-way digest, compared in constant time, expiry bounded, and cleared after successful completion. Provider adapters verify webhook signatures before parsing and require stable external event IDs. `ProviderEvent(provider, external_event_id)` is unique, so a replay is rejected.
- Upload Post timestamped callbacks enforce a bounded age. The legacy callback supports the same production timestamp requirement and derives a body hash when no delivery ID is supplied.
- Publish calls have bounded timeouts. Rate limits carry `Retry-After`; temporary failures use capped exponential backoff. Permanent validation failures are not retried.
- A transport timeout during publish is `UNKNOWN`: status lookup runs before any retry. `SUBMITTED` and `UNKNOWN` jobs are periodically reconciled.
- Disconnect marks scheduled jobs `CONNECTION_REQUIRED` and is refused while publication is actively unresolved.

## API surface

All customer endpoints are below `/api/v3/social/` and use the workspace resolver. Important groups are `studio/home`, `onboarding`, `brand-brain`, `sources`, `story-interview`, `posts`, `variants`, `approvals`, `calendar`, `library`, `connections`, `analytics`, `studio/data-export`, and `studio/data`.

Provider webhooks are public only at `/api/internal/social/publishers/{provider}/webhook/`; they require provider signatures. `/api/internal/social/publishers/health/` requires a staff user and returns only configuration/capability and queue-health flags. OpenAPI is available at `/api/schema/`, `/api/schema/swagger-ui/`, and `/api/schema/redoc/`.

Legacy `/api/v3/linkedin/` routes synchronize through the compatibility service so existing clients and data remain functional during rollout.
