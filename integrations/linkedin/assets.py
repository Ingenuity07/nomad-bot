from urllib.parse import quote

from django.conf import settings
from django.core import signing


ASSET_TOKEN_SALT = "content-automation.post-image"


def image_asset_url(post):
    """Return a tenant-bound bearer URL for publishers that cannot authenticate."""
    if not post.image_data:
        return post.image_url
    token = signing.dumps(
        {"post_id": str(post.id), "settings_id": str(post.settings_id)},
        salt=ASSET_TOKEN_SALT,
        compress=True,
    )
    backend_url = settings.PUBLIC_BACKEND_URL.rstrip("/")
    return f"{backend_url}/api/v3/linkedin/posts/{post.id}/image/?asset_token={quote(token)}"


def asset_token_payload(token):
    return signing.loads(
        token,
        salt=ASSET_TOKEN_SALT,
        max_age=settings.CONTENT_AUTOMATION_ASSET_TOKEN_MAX_AGE_SECONDS,
    )
