from django.conf import settings
from django.core.exceptions import ValidationError
from rest_framework.exceptions import NotAuthenticated, PermissionDenied

from prospecting.models import WorkspaceMembership, get_default_workspace


def development_bootstrap_enabled():
    """Return whether the explicitly configured local/test escape hatch is on."""
    return bool(getattr(settings, "CONTENT_AUTOMATION_DEV_BOOTSTRAP", False))


def resolve_active_workspace(request):
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        memberships = WorkspaceMembership.objects.select_related("workspace").filter(user=user)
        requested_workspace_id = request.headers.get("X-Workspace-ID", "").strip()
        if requested_workspace_id:
            try:
                membership = memberships.filter(workspace_id=requested_workspace_id).first()
            except (ValidationError, ValueError):
                membership = None
            if membership is None:
                raise PermissionDenied("You do not have access to the requested workspace.")
            return membership.workspace

        active_membership = memberships.filter(is_active=True).first()
        if active_membership is not None:
            return active_membership.workspace

        memberships_without_active = list(memberships[:2])
        if len(memberships_without_active) == 1:
            return memberships_without_active[0].workspace
        raise PermissionDenied("Select an active workspace before using content automation.")

    if development_bootstrap_enabled():
        return get_default_workspace()
    raise NotAuthenticated("Authentication is required for content automation.")
