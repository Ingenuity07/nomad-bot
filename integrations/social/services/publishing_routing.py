from dataclasses import dataclass

from django.conf import settings as django_settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from integrations.social.models import (
    ConnectionState,
    PublishJob,
    PublishJobState,
    SocialConnection,
    SocialPostState,
    SocialWorkspaceSettings,
    SocialAuditEventType,
)
from integrations.social.services.audit import record_audit_event
from integrations.social.publishing.errors import (
    ProviderConfigurationError,
    PublishingProviderError,
)
from integrations.social.publishing.registry import publishing_provider_registry
from integrations.social.publishing.types import (
    HealthStatusRequest,
    ProviderCapabilities,
    ProviderName,
    PublishingRouteResult,
    RoutingOutcome,
)
from prospecting.models import WorkspaceMembership


SUPPORTED_PROVIDERS = frozenset(ProviderName)


@dataclass(frozen=True)
class ProviderReadiness:
    provider: ProviderName
    selected: bool
    enabled: bool
    configured: bool
    healthy: bool
    capabilities: ProviderCapabilities
    detail: str = ""


def _provider_name(value):
    try:
        return ProviderName(value)
    except ValueError as exc:
        raise ProviderConfigurationError(
            "Unsupported publishing provider.",
            safe_details={"provider": str(value)},
        ) from exc


def system_default_provider():
    return _provider_name(django_settings.SOCIAL_PUBLISHER_DEFAULT)


def _workspace_policy(workspace):
    # Query explicitly instead of using the reverse one-to-one descriptor;
    # long-lived workspace instances may otherwise retain stale admin changes.
    social_settings = SocialWorkspaceSettings.objects.filter(workspace=workspace).first()
    if social_settings is None:
        return None, frozenset()
    override = (
        _provider_name(social_settings.publishing_provider_override)
        if social_settings.publishing_provider_override
        else None
    )
    supported_values = {provider.value for provider in SUPPORTED_PROVIDERS}
    disabled = frozenset(
        _provider_name(value)
        for value in social_settings.disabled_publishing_providers
        if value in supported_values
    )
    return override, disabled


def selected_provider(workspace):
    override, _ = _workspace_policy(workspace)
    return override or system_default_provider()


def route_social_account(
    *,
    workspace,
    network,
    provider_account_id="",
    provider_profile_id="",
):
    provider = selected_provider(workspace)
    _, disabled = _workspace_policy(workspace)
    if provider in disabled:
        return PublishingRouteResult(
            provider=provider,
            outcome=RoutingOutcome.PROVIDER_DISABLED,
            detail="The selected publishing provider is disabled for this workspace.",
        )

    connections = SocialConnection.objects.filter(
        workspace=workspace,
        network=network,
        provider=provider.value,
        status=ConnectionState.CONNECTED,
    )
    if provider_account_id:
        connections = connections.filter(provider_account_id=provider_account_id)
    if provider_profile_id:
        connections = connections.filter(provider_profile_id=provider_profile_id)
    connection = connections.order_by("created_at").first()
    if connection is None:
        return PublishingRouteResult(
            provider=provider,
            outcome=RoutingOutcome.CONNECTION_REQUIRED,
            detail="Connect the requested social account through the selected provider.",
        )
    return PublishingRouteResult(
        provider=provider,
        outcome=RoutingOutcome.READY,
        connection_id=connection.id,
    )


@transaction.atomic
def create_publish_job(
    *,
    variant,
    approved_version,
    idempotency_key,
    provider_account_id="",
    provider_profile_id="",
):
    if approved_version.variant_id != variant.id:
        raise ValidationError("The approved version must belong to the publish variant.")
    if approved_version.approved_at is None:
        raise ValidationError("The publish job must reference an approved immutable version.")
    if variant.approved_version_id not in {None, approved_version.id}:
        raise ValidationError("The selected approval is no longer current.")

    existing = PublishJob.objects.select_for_update().filter(
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        if existing.variant_id != variant.id or existing.approved_version_id != approved_version.id:
            raise ValidationError("The idempotency key belongs to a different publish job.")
        return PublishingRouteResult(
            provider=_provider_name(existing.provider),
            outcome=RoutingOutcome.READY,
            connection_id=existing.connection_id,
            publish_job_id=existing.id,
        )

    existing = PublishJob.objects.select_for_update().filter(
        variant=variant,
        approved_version=approved_version,
    ).first()
    if existing is not None:
        return PublishingRouteResult(
            provider=_provider_name(existing.provider),
            outcome=RoutingOutcome.READY,
            connection_id=existing.connection_id,
            publish_job_id=existing.id,
        )

    route = route_social_account(
        workspace=variant.post.workspace,
        network=variant.network,
        provider_account_id=provider_account_id,
        provider_profile_id=provider_profile_id,
    )
    if not route.ready:
        variant.status = SocialPostState.CONNECTION_REQUIRED
        variant.failure_reason = route.detail
        variant.save(update_fields=["status", "failure_reason", "updated_at"])
        variant.post.state = SocialPostState.CONNECTION_REQUIRED
        variant.post.save(update_fields=["state", "updated_at"])
        return route

    connection = SocialConnection.objects.get(pk=route.connection_id)
    if variant.approved_version_id is None:
        variant.approved_version = approved_version
    variant.connection = connection
    variant.status = SocialPostState.SCHEDULED
    variant.failure_reason = ""
    variant.save(update_fields=[
        "approved_version", "connection", "status", "failure_reason", "updated_at",
    ])
    variant.post.state = SocialPostState.SCHEDULED
    variant.post.save(update_fields=["state", "updated_at"])
    try:
        with transaction.atomic():
            job = PublishJob.objects.create(
                variant=variant,
                connection=connection,
                approved_version=approved_version,
                provider=route.provider.value,
                idempotency_key=idempotency_key,
                scheduled_for=approved_version.scheduled_for,
                status=PublishJobState.SCHEDULED,
            )
    except IntegrityError:
        conflicting_key = PublishJob.objects.filter(idempotency_key=idempotency_key).first()
        if conflicting_key and (
            conflicting_key.variant_id != variant.id
            or conflicting_key.approved_version_id != approved_version.id
        ):
            raise ValidationError("The idempotency key belongs to a different publish job.")
        job = conflicting_key or PublishJob.objects.get(
            variant=variant,
            approved_version=approved_version,
        )
    record_audit_event(
        workspace=variant.post.workspace,
        event_type=SocialAuditEventType.PUBLISH_JOB_CREATED,
        target=job,
        details={"provider": job.provider, "network": variant.network, "scheduled_for": job.scheduled_for.isoformat()},
    )
    return PublishingRouteResult(
        provider=route.provider,
        outcome=RoutingOutcome.READY,
        connection_id=route.connection_id,
        publish_job_id=job.id,
    )


@transaction.atomic
def create_social_connection(
    *,
    workspace,
    network,
    provider_profile_id,
    provider_account_id,
    display_name,
    account_type,
    capabilities=None,
    status=ConnectionState.CONNECTED,
    provider=None,
):
    # `provider` comes only from trusted callback state so a default change
    # during OAuth cannot relabel the provider that created the connection.
    routed_provider = _provider_name(provider) if provider else selected_provider(workspace)
    _, disabled = _workspace_policy(workspace)
    if routed_provider in disabled:
        return PublishingRouteResult(
            provider=routed_provider,
            outcome=RoutingOutcome.PROVIDER_DISABLED,
            detail="The publishing provider is disabled for this workspace.",
        )
    connection = SocialConnection.objects.create(
        workspace=workspace,
        network=network,
        provider=routed_provider.value,
        provider_profile_id=provider_profile_id,
        provider_account_id=provider_account_id,
        display_name=display_name,
        account_type=account_type,
        capabilities=capabilities or {},
        status=status,
    )
    return PublishingRouteResult(
        provider=routed_provider,
        outcome=RoutingOutcome.READY,
        connection_id=connection.id,
    )


def provider_readiness(workspace=None):
    selected = selected_provider(workspace) if workspace is not None else system_default_provider()
    disabled = _workspace_policy(workspace)[1] if workspace is not None else frozenset()
    rows = []
    for provider_name in publishing_provider_registry.registered_providers():
        adapter = publishing_provider_registry.create(provider_name)
        try:
            health = adapter.health_status(HealthStatusRequest(
                workspace_id=workspace.id if workspace is not None else None,
            ))
            configured = health.configured
            healthy = health.healthy
            detail = health.detail
            capabilities = health.capabilities
        except PublishingProviderError as exc:
            configured = False
            healthy = False
            detail = str(exc)
            capabilities = adapter.capabilities
        rows.append(ProviderReadiness(
            provider=provider_name,
            selected=provider_name == selected,
            enabled=provider_name not in disabled,
            configured=configured,
            healthy=healthy,
            capabilities=capabilities,
            detail=detail,
        ))
    return tuple(rows)


class SocialPublisherAdminService:
    @staticmethod
    def _require_workspace_admin(actor, workspace):
        if not getattr(actor, "is_authenticated", False):
            raise PermissionDenied("Authentication is required.")
        if getattr(actor, "is_superuser", False):
            return
        allowed = WorkspaceMembership.objects.filter(
            user=actor,
            workspace=workspace,
            role__in=[WorkspaceMembership.OWNER, WorkspaceMembership.ADMIN],
        ).exists()
        if not allowed:
            raise PermissionDenied("Workspace administrator access is required.")

    @classmethod
    @transaction.atomic
    def set_workspace_override(cls, *, actor, workspace, provider=None):
        cls._require_workspace_admin(actor, workspace)
        provider_name = _provider_name(provider) if provider else None
        social_settings, _ = SocialWorkspaceSettings.objects.get_or_create(workspace=workspace)
        social_settings.publishing_provider_override = provider_name.value if provider_name else ""
        social_settings.full_clean()
        social_settings.save(update_fields=["publishing_provider_override", "updated_at"])
        record_audit_event(
            workspace=workspace,
            event_type=SocialAuditEventType.PROVIDER_POLICY_CHANGED,
            actor=actor,
            target=social_settings,
            details={"change": "default_override", "provider": provider_name.value if provider_name else "system_default"},
        )
        return social_settings

    @classmethod
    @transaction.atomic
    def set_provider_enabled(cls, *, actor, workspace, provider, enabled):
        cls._require_workspace_admin(actor, workspace)
        provider_name = _provider_name(provider)
        social_settings, _ = SocialWorkspaceSettings.objects.get_or_create(workspace=workspace)
        disabled = set(social_settings.disabled_publishing_providers)
        if enabled:
            disabled.discard(provider_name.value)
        else:
            disabled.add(provider_name.value)
        social_settings.disabled_publishing_providers = sorted(disabled)
        social_settings.full_clean()
        social_settings.save(update_fields=["disabled_publishing_providers", "updated_at"])
        record_audit_event(
            workspace=workspace,
            event_type=SocialAuditEventType.PROVIDER_POLICY_CHANGED,
            actor=actor,
            target=social_settings,
            details={"change": "provider_enabled", "provider": provider_name.value, "enabled": enabled},
        )
        return social_settings

    @classmethod
    def readiness(cls, *, actor, workspace):
        cls._require_workspace_admin(actor, workspace)
        return provider_readiness(workspace)
