from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from integrations.social.models import MediaAsset


@receiver(post_delete, sender=MediaAsset)
def remove_media_files_after_commit(sender, instance, **kwargs):
    if not instance.original_storage_key and not instance.publish_storage_key:
        return
    from integrations.social.media import _delete_stored_files

    transaction.on_commit(
        lambda: _delete_stored_files(
            instance.original_storage_key,
            instance.publish_storage_key,
        )
    )
