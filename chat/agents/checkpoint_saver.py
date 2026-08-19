from typing import Any, Iterator, Optional, Sequence, Tuple, Union, AsyncIterator
import threading
from asgiref.sync import sync_to_async
from langchain_core.runnables import RunnableConfig

_db_write_lock = threading.RLock()

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
)

def _serialize(serde, obj) -> bytes:
    if obj is None:
        return b"null:"
    type_, data = serde.dumps_typed(obj)
    return type_.encode("ascii") + b":" + data

def _deserialize(serde, serialized: bytes) -> Any:
    serialized_bytes = bytes(serialized)
    if serialized_bytes == b"null:":
        return None
    type_bytes, data = serialized_bytes.split(b":", 1)
    type_ = type_bytes.decode("ascii")
    return serde.loads_typed((type_, data))

class DjangoCheckpointSaver(BaseCheckpointSaver):
    """
    A custom LangGraph checkpoint saver that persists checkpoints and writes
    directly inside Django database models.
    """

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        with _db_write_lock:
            thread_id = config["configurable"]["thread_id"]
            checkpoint_id = checkpoint["id"]
            parent_checkpoint_id = config["configurable"].get("checkpoint_id")

            checkpoint_data = _serialize(self.serde, checkpoint)
            metadata_data = _serialize(self.serde, metadata)

            from chat.models import AgentCheckpoint
            AgentCheckpoint.objects.update_or_create(
                thread_id=thread_id,
                checkpoint_id=checkpoint_id,
                defaults={
                    "parent_checkpoint_id": parent_checkpoint_id,
                    "checkpoint_data": checkpoint_data,
                    "metadata_data": metadata_data,
                }
            )

            return {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                }
            }

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await sync_to_async(self.put)(config, checkpoint, metadata, new_versions)

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = config["configurable"].get("checkpoint_id")

        from chat.models import AgentCheckpoint, AgentCheckpointWrite

        if checkpoint_id:
            checkpoint_obj = AgentCheckpoint.objects.filter(
                thread_id=thread_id,
                checkpoint_id=checkpoint_id
            ).first()
        else:
            checkpoint_obj = AgentCheckpoint.objects.filter(
                thread_id=thread_id
            ).first()

        if not checkpoint_obj:
            return None

        checkpoint = _deserialize(self.serde, checkpoint_obj.checkpoint_data)
        metadata = _deserialize(self.serde, checkpoint_obj.metadata_data)

        # Retrieve writes
        write_objs = AgentCheckpointWrite.objects.filter(
            thread_id=thread_id,
            checkpoint_id=checkpoint_obj.checkpoint_id
        )
        pending_writes = []
        for w in write_objs:
            val = _deserialize(self.serde, w.value)
            pending_writes.append((w.task_id, w.channel, val))

        parent_config = None
        if checkpoint_obj.parent_checkpoint_id:
            parent_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_obj.parent_checkpoint_id,
                }
            }

        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_obj.checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes
        )

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return await sync_to_async(self.get_tuple)(config)

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        from chat.models import AgentCheckpoint

        query = AgentCheckpoint.objects.all()
        if config and "configurable" in config and "thread_id" in config["configurable"]:
            query = query.filter(thread_id=config["configurable"]["thread_id"])

        if before and "configurable" in before and "checkpoint_id" in before["configurable"]:
            before_checkpoint = AgentCheckpoint.objects.filter(
                thread_id=before["configurable"]["thread_id"],
                checkpoint_id=before["configurable"]["checkpoint_id"]
            ).first()
            if before_checkpoint:
                query = query.filter(created_at__lt=before_checkpoint.created_at)

        if limit:
            query = query[:limit]

        for checkpoint_obj in query:
            checkpoint = _deserialize(self.serde, checkpoint_obj.checkpoint_data)
            metadata = _deserialize(self.serde, checkpoint_obj.metadata_data)

            # Retrieve writes
            from chat.models import AgentCheckpointWrite
            write_objs = AgentCheckpointWrite.objects.filter(
                thread_id=checkpoint_obj.thread_id,
                checkpoint_id=checkpoint_obj.checkpoint_id
            )
            pending_writes = []
            for w in write_objs:
                val = _deserialize(self.serde, w.value)
                pending_writes.append((w.task_id, w.channel, val))

            parent_config = None
            if checkpoint_obj.parent_checkpoint_id:
                parent_config = {
                    "configurable": {
                        "thread_id": checkpoint_obj.thread_id,
                        "checkpoint_id": checkpoint_obj.parent_checkpoint_id,
                    }
                }

            yield CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": checkpoint_obj.thread_id,
                        "checkpoint_id": checkpoint_obj.checkpoint_id,
                    }
                },
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=parent_config,
                pending_writes=pending_writes
            )

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        tuples = await sync_to_async(list)(
            self.list(config, filter=filter, before=before, limit=limit)
        )
        for t in tuples:
            yield t

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        with _db_write_lock:
            thread_id = config["configurable"]["thread_id"]
            checkpoint_id = config["configurable"]["checkpoint_id"]

            from chat.models import AgentCheckpointWrite

            for idx, (channel, value) in enumerate(writes):
                val_data = _serialize(self.serde, value)
                AgentCheckpointWrite.objects.update_or_create(
                    thread_id=thread_id,
                    checkpoint_id=checkpoint_id,
                    task_id=task_id,
                    idx=idx,
                    defaults={
                        "channel": channel,
                        "value": val_data
                    }
                )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await sync_to_async(self.put_writes)(config, writes, task_id, task_path)
