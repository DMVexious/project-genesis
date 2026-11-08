"""NPC memory: what happened between two NPCs, used for grudges and alliances.

``ChromaMemory`` stores memories in a local ChromaDB collection and retrieves
them with a vector query filtered to the (self, other) pair. ``SimpleMemory``
is an in-process fallback with the same interface for when ChromaDB is not
installed.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Memory:
    owner: int
    other: int
    tick: int
    kind: str
    text: str
    valence: int  # negative = grudge, positive = alliance


class SimpleMemory:
    name = "simple"

    def __init__(self):
        self._items: list[Memory] = []

    def log(self, m: Memory):
        self._items.append(m)

    def recall(self, owner: int, other: int, k: int = 3) -> list[Memory]:
        hits = [m for m in self._items if m.owner == owner and m.other == other]
        return hits[-k:]

    def grudge(self, owner: int, other: int) -> int:
        return sum(m.valence for m in self._items if m.owner == owner and m.other == other)

    def flush(self):
        pass

    def count(self) -> int:
        return len(self._items)

    def reset(self):
        self._items.clear()


class ChromaMemory:
    name = "chromadb"
    blocking = True  # embedding is slow: callers should run recall()/flush() in a thread

    def __init__(self, path: str | None = None, collection: str = "genesis_memories"):
        import chromadb  # optional dependency
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        self._client = chromadb.PersistentClient(path=path) if path else chromadb.EphemeralClient()
        self._collection_name = collection
        self._ef = DefaultEmbeddingFunction()
        self._col = self._client.get_or_create_collection(collection, embedding_function=self._ef)
        self._query_vec = None  # the recall query never changes, so embed it once
        self._n = 0       # ids handed out (unique per process)
        self._stored = 0  # documents in the collection
        # flush() runs in a worker thread (embedding is slow); the lock keeps ids
        # and counts consistent with reset(). recall() never waits for it.
        self._lock = threading.RLock()
        self._grudges: dict[tuple[int, int], int] = {}
        self._pending: list[Memory] = []
        self._embedding: list[Memory] = []  # the batch flush() is embedding right now

    def log(self, m: Memory):
        # Buffered: embedding one document per call is slow, so the sim calls
        # flush() once per tick to embed and store the whole batch.
        self._pending.append(m)
        key = (m.owner, m.other)
        self._grudges[key] = self._grudges.get(key, 0) + m.valence

    def flush(self):
        with self._lock:
            if not self._pending:
                return
            batch, self._pending = self._pending, []
            self._embedding = batch
            ids = []
            for m in batch:
                self._n += 1
                ids.append(f"m{self._n}-{m.tick}-{m.owner}-{m.other}")
            self._col.add(
                ids=ids,
                documents=[m.text for m in batch],
                metadatas=[{"owner": m.owner, "other": m.other, "tick": m.tick,
                            "kind": m.kind, "valence": m.valence} for m in batch],
            )
            self._stored += len(batch)
            self._embedding = []

    def recall(self, owner: int, other: int, k: int = 3) -> list[Memory]:
        # Memories not embedded yet are matched directly instead of waiting for
        # flush(): under load the embedding backlog could take seconds.
        recent = [m for m in self._embedding + self._pending if m.owner == owner and m.other == other]
        stored: list[Memory] = []
        where = {"$and": [{"owner": owner}, {"other": other}]}
        try:
            if self._query_vec is None:
                self._query_vec = self._ef(["fight attack trade child friend"])[0]
            res = self._col.query(query_embeddings=[self._query_vec], n_results=k, where=where)
            stored = [Memory(md["owner"], md["other"], md["tick"], md["kind"], d, md["valence"])
                      for d, md in zip(res["documents"][0], res["metadatas"][0])]
        except Exception as e:  # no matching memories, or embedding backend unavailable
            log.debug("chroma query failed: %s", e)
        unique = {(m.tick, m.kind, m.text): m for m in stored + recent}  # a batch may land mid-call
        return sorted(unique.values(), key=lambda m: m.tick)[-k:]

    def grudge(self, owner: int, other: int) -> int:
        return self._grudges.get((owner, other), 0)

    def count(self) -> int:
        return self._stored + len(self._pending)  # no DB round trip on every frame

    def reset(self):
        with self._lock:
            self._pending = []
            self._embedding = []
            self._client.delete_collection(self._collection_name)
            self._col = self._client.get_or_create_collection(self._collection_name, embedding_function=self._ef)
            self._grudges.clear()
            self._stored = 0


def make_memory(kind: str = "auto", path: str | None = None):
    """kind: 'auto' (ChromaDB if installed), 'chromadb', or 'simple'."""
    if kind == "simple":
        return SimpleMemory()
    try:
        return ChromaMemory(path)
    except ImportError:
        if kind == "chromadb":
            raise
        log.info("chromadb not installed; using in-memory store")
        return SimpleMemory()
