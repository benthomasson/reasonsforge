"""Cross-domain investigation topics queue for meta forge."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class Topic:
    """A queued investigation topic."""
    title: str
    kind: str       # cross-domain, contradiction, derivation, general
    target: str     # belief ID, agent pair, etc.
    source: str = ""
    status: str = "pending"
    added: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


TOPIC_KINDS = {"cross-domain", "contradiction", "derivation", "general"}

from . import META_DIR


def _queue_path(meta_dir: str | None = None) -> str:
    if meta_dir is None:
        meta_dir = META_DIR
    return os.path.join(meta_dir, "topics.json")


def load_queue(meta_dir: str | None = None) -> list[Topic]:
    path = _queue_path(meta_dir)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [Topic(**item) for item in data]


def save_queue(queue: list[Topic], meta_dir: str | None = None) -> None:
    if meta_dir is None:
        meta_dir = META_DIR
    os.makedirs(meta_dir, exist_ok=True)
    path = _queue_path(meta_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(t) for t in queue], f, indent=2)


def add_topics(topics: list[Topic], meta_dir: str | None = None) -> int:
    queue = load_queue(meta_dir)
    existing_targets = {t.target for t in queue}
    added = 0
    for topic in topics:
        if topic.target not in existing_targets:
            queue.append(topic)
            existing_targets.add(topic.target)
            added += 1
    if added:
        save_queue(queue, meta_dir)
    return added


def pop_next(meta_dir: str | None = None) -> Topic | None:
    queue = load_queue(meta_dir)
    for topic in queue:
        if topic.status == "pending":
            topic.status = "done"
            save_queue(queue, meta_dir)
            return topic
    return None


def pop_at(index: int, meta_dir: str | None = None) -> Topic | None:
    queue = load_queue(meta_dir)
    pending = [i for i, t in enumerate(queue) if t.status == "pending"]
    if index < 0 or index >= len(pending):
        return None
    queue[pending[index]].status = "done"
    save_queue(queue, meta_dir)
    return queue[pending[index]]


def skip_topic(index: int, meta_dir: str | None = None) -> bool:
    queue = load_queue(meta_dir)
    pending = [i for i, t in enumerate(queue) if t.status == "pending"]
    if index < 0 or index >= len(pending):
        return False
    queue[pending[index]].status = "skipped"
    save_queue(queue, meta_dir)
    return True


def pending_count(meta_dir: str | None = None) -> int:
    return sum(1 for t in load_queue(meta_dir) if t.status == "pending")


# --- Parsing topics from model output ---

TOPIC_LINE_PATTERN = re.compile(
    r"^[-*]\s+"
    r"\[(\w[\w-]*)\]\s+"
    r"`([^`]+)`"
    r"\s*(?:—|-|:)\s*"
    r"(.+)$",
    re.MULTILINE,
)


def parse_topics_from_response(response: str, source: str = "") -> list[Topic]:
    section_match = re.search(
        r"#+\s*Topics?\s+to\s+Explore\s*\n(.*?)(?=\n#|\Z)",
        response,
        re.DOTALL | re.IGNORECASE,
    )
    if not section_match:
        return []

    section_text = section_match.group(1)
    topics = []

    for match in TOPIC_LINE_PATTERN.finditer(section_text):
        kind = match.group(1).lower()
        target = match.group(2)
        title = match.group(3).strip()

        if kind not in TOPIC_KINDS:
            kind = "general"

        topics.append(Topic(
            title=title,
            kind=kind,
            target=target,
            source=source,
        ))

    return topics
