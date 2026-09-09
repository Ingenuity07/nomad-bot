import hashlib
import ipaddress
import struct
import uuid
from dataclasses import dataclass
from pathlib import PurePath
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import storages
from django.db import transaction

from integrations.social.models import (
    MediaAsset,
    MediaAssetSource,
    MediaAssetType,
    SocialNetwork,
)
from integrations.social.publishing.errors import ProviderValidationError
from integrations.social.publishing.types import PublishingMediaType, PublishingNetwork


@dataclass(frozen=True)
class MediaPolicy:
    max_images: int
    max_image_bytes: int
    max_image_pixels: int
    max_video_bytes: int
    max_video_duration_ms: int
    min_video_bytes: int
    min_video_duration_ms: int
    max_document_bytes: int
    allows_video: bool
    allows_document: bool
    image_content_types: frozenset[str]
    video_content_types: frozenset[str]
    document_content_types: frozenset[str]


PLATFORM_MEDIA_POLICIES = {
    SocialNetwork.LINKEDIN: MediaPolicy(
        max_images=20,
        max_image_bytes=8 * 1024 * 1024,
        max_image_pixels=36_152_320,
        max_video_bytes=500 * 1024 * 1024,
        max_video_duration_ms=30 * 60 * 1000,
        min_video_bytes=75 * 1024,
        min_video_duration_ms=3 * 1000,
        max_document_bytes=100 * 1024 * 1024,
        allows_video=True,
        allows_document=True,
        image_content_types=frozenset({"image/jpeg", "image/png", "image/gif"}),
        video_content_types=frozenset({"video/mp4"}),
        document_content_types=frozenset({"application/pdf"}),
    ),
    SocialNetwork.X: MediaPolicy(
        max_images=4,
        max_image_bytes=5 * 1024 * 1024,
        max_image_pixels=40_000_000,
        max_video_bytes=8 * 1024 * 1024 * 1024,
        max_video_duration_ms=20 * 60 * 1000,
        min_video_bytes=1,
        min_video_duration_ms=1,
        max_document_bytes=0,
        allows_video=True,
        allows_document=False,
        image_content_types=frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"}),
        video_content_types=frozenset({"video/mp4", "video/quicktime"}),
        document_content_types=frozenset(),
    ),
    SocialNetwork.INSTAGRAM: MediaPolicy(
        max_images=10,
        max_image_bytes=8 * 1024 * 1024,
        max_image_pixels=40_000_000,
        max_video_bytes=100 * 1024 * 1024,
        max_video_duration_ms=15 * 60 * 1000,
        min_video_bytes=1,
        min_video_duration_ms=1,
        max_document_bytes=0,
        allows_video=True,
        allows_document=False,
        image_content_types=frozenset({"image/jpeg"}),
        video_content_types=frozenset({"video/mp4", "video/quicktime"}),
        document_content_types=frozenset(),
    ),
}

MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "application/pdf": ".pdf",
}


@dataclass(frozen=True)
class MediaProbe:
    asset_type: str
    content_type: str
    byte_size: int
    checksum_sha256: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None


class MediaValidationError(ValidationError):
    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__(list(self.issues))


def _jpeg_dimensions(data):
    offset = 2
    while offset + 9 < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            break
        length = struct.unpack(">H", data[offset:offset + 2])[0]
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            return struct.unpack(">HH", data[offset + 3:offset + 7])[::-1]
        if length < 2:
            break
        offset += length
    return None, None


def _mp4_metadata(data):
    duration_ms = None
    width = height = None
    mvhd = data.find(b"mvhd")
    if mvhd >= 0 and mvhd + 32 <= len(data):
        version = data[mvhd + 4]
        if version == 0:
            timescale = struct.unpack(">I", data[mvhd + 16:mvhd + 20])[0]
            duration = struct.unpack(">I", data[mvhd + 20:mvhd + 24])[0]
        else:
            timescale = struct.unpack(">I", data[mvhd + 24:mvhd + 28])[0]
            duration = struct.unpack(">Q", data[mvhd + 28:mvhd + 36])[0]
        if timescale:
            duration_ms = int(duration * 1000 / timescale)
    tkhd = data.find(b"tkhd")
    if tkhd >= 0:
        version = data[tkhd + 4] if tkhd + 4 < len(data) else 0
        dimension_offset = tkhd + (88 if version else 76)
        if dimension_offset + 8 <= len(data):
            width = struct.unpack(">I", data[dimension_offset:dimension_offset + 4])[0] >> 16
            height = struct.unpack(">I", data[dimension_offset + 4:dimension_offset + 8])[0] >> 16
    return width or None, height or None, duration_ms


def _detect_media(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return MediaAssetType.IMAGE, "image/png", width, height, None
    if data.startswith(b"\xff\xd8"):
        width, height = _jpeg_dimensions(data)
        return MediaAssetType.IMAGE, "image/jpeg", width, height, None
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return MediaAssetType.IMAGE, "image/gif", width, height, None
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        width = height = None
        if data[12:16] == b"VP8X" and len(data) >= 30:
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
        return MediaAssetType.IMAGE, "image/webp", width, height, None
    if len(data) >= 12 and data[4:8] == b"ftyp":
        width, height, duration = _mp4_metadata(data)
        content_type = "video/quicktime" if data[8:12] == b"qt  " else "video/mp4"
        return MediaAssetType.VIDEO, content_type, width, height, duration
    if data.startswith(b"%PDF-"):
        return MediaAssetType.DOCUMENT, "application/pdf", None, None, None
    raise MediaValidationError(["The uploaded file type is not supported."])


def probe_uploaded_file(uploaded_file):
    size = int(getattr(uploaded_file, "size", 0) or 0)
    if size <= 0:
        raise MediaValidationError(["The uploaded file is empty."])
    maximum = max(
        max(policy.max_video_bytes, policy.max_document_bytes, policy.max_image_bytes)
        for policy in PLATFORM_MEDIA_POLICIES.values()
    )
    if size > maximum:
        raise MediaValidationError(["The uploaded file exceeds the maximum supported size."])
    digest = hashlib.sha256()
    header = bytearray()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
        if len(header) < 2 * 1024 * 1024:
            remaining = (2 * 1024 * 1024) - len(header)
            header.extend(chunk[:remaining])
    uploaded_file.seek(0)
    asset_type, detected_type, width, height, duration = _detect_media(bytes(header))
    declared_type = str(getattr(uploaded_file, "content_type", "") or "").lower()
    if declared_type and declared_type != "application/octet-stream" and declared_type != detected_type:
        raise MediaValidationError(["The file contents do not match the declared content type."])
    return MediaProbe(
        asset_type=asset_type,
        content_type=detected_type,
        byte_size=size,
        checksum_sha256=digest.hexdigest(),
        width=width,
        height=height,
        duration_ms=duration,
    )


def _item_value(item, name, default=None):
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def media_validation_issues(network, items, provider_capabilities=None):
    network = SocialNetwork(network)
    policy = PLATFORM_MEDIA_POLICIES[network]
    items = list(items)
    if not items:
        return []
    asset_types = [str(_item_value(item, "asset_type")) for item in items]
    type_set = set(asset_types)
    issues = []
    if MediaAssetType.MULTI_IMAGE in type_set:
        issues.append("Multiple-image collections must contain individual IMAGE assets.")
    normalized_types = {MediaAssetType.IMAGE if value == MediaAssetType.MULTI_IMAGE else value for value in type_set}
    if len(normalized_types) > 1:
        issues.append("Images, video and documents cannot be mixed in one platform variant.")
    image_count = sum(value in {MediaAssetType.IMAGE, MediaAssetType.MULTI_IMAGE} for value in asset_types)
    video_count = asset_types.count(MediaAssetType.VIDEO)
    document_count = asset_types.count(MediaAssetType.DOCUMENT)
    if image_count > policy.max_images:
        issues.append(f"{network.label} supports at most {policy.max_images} images per post.")
    if video_count > 1 or document_count > 1:
        issues.append("A post can contain only one video or one document.")
    if video_count and not policy.allows_video:
        issues.append(f"{network.label} does not support video in this workflow.")
    if document_count and not policy.allows_document:
        issues.append(f"{network.label} does not support document posts.")
    for item in items:
        asset_type = str(_item_value(item, "asset_type"))
        byte_size = int(_item_value(item, "byte_size", 0) or 0)
        width = _item_value(item, "width")
        height = _item_value(item, "height")
        duration_ms = _item_value(item, "duration_ms")
        content_type = str(_item_value(item, "content_type", "") or "")
        if asset_type in {MediaAssetType.IMAGE, MediaAssetType.MULTI_IMAGE}:
            if content_type and content_type not in policy.image_content_types:
                issues.append(f"{content_type} images are not supported on {network.label}.")
            if byte_size > policy.max_image_bytes:
                issues.append(f"Each {network.label} image must be no larger than {policy.max_image_bytes} bytes.")
            if not width or not height:
                issues.append("Image dimensions could not be verified.")
            elif width * height >= policy.max_image_pixels:
                issues.append(f"The image exceeds the {network.label} pixel limit.")
        elif asset_type == MediaAssetType.VIDEO:
            if content_type not in policy.video_content_types:
                issues.append(f"{content_type or 'This video type'} is not supported on {network.label}.")
            if byte_size > policy.max_video_bytes:
                issues.append(f"The {network.label} video is too large.")
            if byte_size < policy.min_video_bytes:
                issues.append(f"The {network.label} video is too small.")
            if not width or not height:
                issues.append("Video dimensions could not be verified.")
            if duration_ms is None:
                issues.append("Video duration could not be verified.")
            else:
                if duration_ms > policy.max_video_duration_ms:
                    issues.append(f"The {network.label} video is too long.")
                if duration_ms < policy.min_video_duration_ms:
                    issues.append(f"The {network.label} video is too short.")
        elif asset_type == MediaAssetType.DOCUMENT:
            if content_type not in policy.document_content_types:
                issues.append(f"{content_type or 'This document type'} is not supported on {network.label}.")
            if byte_size > policy.max_document_bytes:
                issues.append(f"The {network.label} document is too large.")
    if provider_capabilities is not None:
        publishing_network = PublishingNetwork(network)
        if publishing_network not in provider_capabilities.networks:
            issues.append(f"The selected publishing connection does not support {network.label}.")
        elif image_count:
            required = PublishingMediaType.MULTI_IMAGE if image_count > 1 else PublishingMediaType.IMAGE
            if required not in provider_capabilities.media_types:
                issues.append("The selected publishing connection does not support this image count.")
        elif video_count and PublishingMediaType.VIDEO not in provider_capabilities.media_types:
            issues.append("The selected publishing connection does not support video.")
        elif document_count and PublishingMediaType.DOCUMENT not in provider_capabilities.media_types:
            issues.append("The selected publishing connection does not support documents.")
    return list(dict.fromkeys(issues))


def validate_variant_media(variant, provider_capabilities=None):
    assets = list(variant.media_assets.order_by("sort_order", "created_at"))
    issues = media_validation_issues(
        variant.network,
        assets,
        provider_capabilities,
    )
    for asset in assets:
        if not asset.publish_url or not is_allowed_publish_url(asset.publish_url):
            issues.append("A media derivative is missing or is not hosted in approved storage.")
        if asset.publish_storage_key and not storages["social_publish"].exists(asset.publish_storage_key):
            issues.append("A media derivative is no longer available in publish storage.")
    if issues:
        raise MediaValidationError(list(dict.fromkeys(issues)))


def validate_version_media(version, provider_capabilities):
    issues = media_validation_issues(version.variant.network, version.media_snapshot, provider_capabilities)
    for item in version.media_snapshot:
        url = str(item.get("storage_url") or "")
        if not url or not is_allowed_publish_url(url):
            issues.append("A media derivative is missing or is not hosted in approved storage.")
        publish_key = str(item.get("publish_storage_key") or "")
        if publish_key and not storages["social_publish"].exists(publish_key):
            issues.append("A media derivative is no longer available in publish storage.")
    if issues:
        raise ProviderValidationError(
            "The approved media is not valid for this publishing connection.",
            safe_details={"media_errors": list(dict.fromkeys(issues))},
        )


def _safe_filename(filename):
    name = PurePath(str(filename or "media")).name
    stem = "".join(character for character in PurePath(name).stem if character.isalnum() or character in "-_")[:80]
    return stem or "media"


def publish_url_for_asset(asset):
    if asset.publish_storage_key:
        return storages["social_publish"].url(asset.publish_storage_key)
    return asset.storage_url


def _trusted_publish_hosts():
    hosts = {
        str(host).strip().lower()
        for host in getattr(settings, "SOCIAL_MEDIA_EXTERNAL_URL_ALLOWLIST", ())
        if str(host).strip()
    }
    for url in (
        getattr(settings, "PUBLIC_BACKEND_URL", ""),
        storages["social_publish"].url("social-content-probe"),
    ):
        parsed = urlparse(str(url))
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    return hosts


def is_allowed_publish_url(url):
    parsed = urlparse(str(url))
    publish_probe = urlparse(storages["social_publish"].url("social-content-probe"))
    allowed_paths = {
        f"{settings.MEDIA_URL.rstrip('/')}/",
        publish_probe.path.rsplit("/", 1)[0] + "/",
        "/api/v3/linkedin/posts/",
    }
    if not parsed.scheme and str(url).startswith("/"):
        return any(parsed.path.startswith(prefix) for prefix in allowed_paths)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local):
        return False
    if hostname not in _trusted_publish_hosts():
        return False
    public_backend_host = urlparse(str(getattr(settings, "PUBLIC_BACKEND_URL", ""))).hostname
    if public_backend_host and hostname == public_backend_host.lower():
        return any(parsed.path.startswith(prefix) for prefix in allowed_paths)
    return True


def safe_publish_url(url):
    if not is_allowed_publish_url(url):
        raise ProviderValidationError("A media derivative is not hosted in approved storage.")
    if str(url).startswith("/"):
        return urljoin(f"{settings.PUBLIC_BACKEND_URL.rstrip('/')}/", str(url).lstrip("/"))
    return str(url)


@transaction.atomic
def store_uploaded_media(
    variant,
    uploaded_file,
    *,
    alt_text="",
    source=MediaAssetSource.UPLOAD,
    replace_asset=None,
):
    from integrations.social.services.lifecycle import edit_variant

    variant = type(variant).objects.select_for_update().select_related("post").get(pk=variant.pk)
    probe = probe_uploaded_file(uploaded_file)
    current = list(variant.media_assets.select_for_update().order_by("sort_order", "created_at"))
    replacement = None
    if replace_asset is not None:
        replacement = next((asset for asset in current if asset.pk == replace_asset.pk), None)
        if replacement is None or replacement.asset_type != MediaAssetType.IMAGE:
            raise MediaValidationError(["The image being replaced does not belong to this variant."])
        current = [asset for asset in current if asset.pk != replacement.pk]
    preview = current + [{
        "asset_type": probe.asset_type,
        "content_type": probe.content_type,
        "byte_size": probe.byte_size,
        "width": probe.width,
        "height": probe.height,
        "duration_ms": probe.duration_ms,
    }]
    issues = media_validation_issues(variant.network, preview)
    if issues:
        raise MediaValidationError(issues)

    extension = MIME_EXTENSIONS[probe.content_type]
    original_key = (
        f"social-content/{variant.post.workspace_id}/{variant.id}/originals/"
        f"{uuid.uuid4().hex}-{_safe_filename(uploaded_file.name)}{extension}"
    )
    publish_key = (
        f"social-content/{variant.post.workspace_id}/{variant.id}/publish/"
        f"{probe.checksum_sha256}{extension}"
    )
    private_storage = storages["social_private"]
    publish_storage = storages["social_publish"]
    original_key = private_storage.save(original_key, uploaded_file)
    uploaded_file.seek(0)
    if not publish_storage.exists(publish_key):
        publish_key = publish_storage.save(publish_key, uploaded_file)
    try:
        sort_order = replacement.sort_order if replacement else len(current)
        if replacement:
            replacement.delete()
        asset = MediaAsset.objects.create(
            workspace=variant.post.workspace,
            variant=variant,
            post=variant.post,
            asset_type=probe.asset_type,
            source=source,
            original_filename=PurePath(str(uploaded_file.name)).name[:255],
            original_storage_key=original_key,
            publish_storage_key=publish_key,
            content_type=probe.content_type,
            byte_size=probe.byte_size,
            checksum_sha256=probe.checksum_sha256,
            width=probe.width,
            height=probe.height,
            duration_ms=probe.duration_ms,
            alt_text=str(alt_text or "")[:500],
            sort_order=sort_order,
        )
    except Exception:
        private_storage.delete(original_key)
        if not MediaAsset.objects.filter(publish_storage_key=publish_key).exists():
            publish_storage.delete(publish_key)
        raise
    edit_variant(variant, media_changed=True)
    return asset


@transaction.atomic
def update_media_alt_text(asset, alt_text):
    from integrations.social.services.lifecycle import edit_variant

    asset = MediaAsset.objects.select_for_update().select_related("variant").get(pk=asset.pk)
    value = str(alt_text or "")[:500]
    if asset.alt_text != value:
        asset.alt_text = value
        asset.save(update_fields=["alt_text", "updated_at"])
        edit_variant(asset.variant, media_changed=True)
    return asset


@transaction.atomic
def reorder_variant_images(variant, asset_ids):
    from integrations.social.services.lifecycle import edit_variant

    variant = type(variant).objects.select_for_update().get(pk=variant.pk)
    assets = list(variant.media_assets.select_for_update().order_by("sort_order", "created_at"))
    if any(asset.asset_type != MediaAssetType.IMAGE for asset in assets):
        raise MediaValidationError(["Only image collections can be reordered."])
    requested = [str(value) for value in asset_ids]
    if len(requested) != len(set(requested)) or set(requested) != {str(asset.id) for asset in assets}:
        raise MediaValidationError(["Provide every image asset exactly once."])
    by_id = {str(asset.id): asset for asset in assets}
    if requested == [str(asset.id) for asset in assets]:
        return assets
    for offset, asset in enumerate(assets, start=len(assets) + 1):
        MediaAsset.objects.filter(pk=asset.pk).update(sort_order=offset)
    for index, asset_id in enumerate(requested):
        MediaAsset.objects.filter(pk=by_id[asset_id].pk).update(sort_order=index)
    edit_variant(variant, media_changed=True)
    return list(variant.media_assets.order_by("sort_order", "created_at"))


@transaction.atomic
def delete_media_asset(asset):
    from integrations.social.services.lifecycle import edit_variant

    asset = MediaAsset.objects.select_for_update().select_related("variant").get(pk=asset.pk)
    variant = asset.variant
    asset.delete()
    remaining = list(variant.media_assets.order_by("sort_order", "created_at"))
    for index, item in enumerate(remaining):
        if item.sort_order != index:
            MediaAsset.objects.filter(pk=item.pk).update(sort_order=index)
    edit_variant(variant, media_changed=True)


def _delete_stored_files(original_key, publish_key):
    if original_key:
        storages["social_private"].delete(original_key)
    if publish_key and not MediaAsset.objects.filter(publish_storage_key=publish_key).exists():
        storages["social_publish"].delete(publish_key)
