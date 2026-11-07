"""Tests for requeue_topics in the code forge topics queue."""

import json

from reasonsforge.forge.code.topics import (
    Topic,
    add_topics,
    load_queue,
    pop_batch,
    requeue_topics,
    save_queue,
)


def test_requeue_sets_done_back_to_pending(tmp_path):
    project_dir = str(tmp_path)
    topics = [
        Topic(title="Explain auth", kind="file", target="src/auth.py"),
        Topic(title="Explain router", kind="file", target="src/router.py"),
    ]
    add_topics(topics, project_dir)
    batch = pop_batch(2, project_dir)
    assert len(batch) == 2

    queue = load_queue(project_dir)
    assert all(t.status == "done" for t in queue)

    count = requeue_topics([batch[0]], project_dir)
    assert count == 1

    queue = load_queue(project_dir)
    auth = next(t for t in queue if t.target == "src/auth.py")
    router = next(t for t in queue if t.target == "src/router.py")
    assert auth.status == "pending"
    assert router.status == "done"


def test_requeue_no_match_returns_zero(tmp_path):
    project_dir = str(tmp_path)
    topics = [Topic(title="Explain auth", kind="file", target="src/auth.py")]
    add_topics(topics, project_dir)
    pop_batch(1, project_dir)

    unrelated = Topic(title="Other", kind="general", target="something-else")
    count = requeue_topics([unrelated], project_dir)
    assert count == 0

    queue = load_queue(project_dir)
    assert queue[0].status == "done"


def test_requeue_skips_pending_topics(tmp_path):
    project_dir = str(tmp_path)
    topics = [
        Topic(title="A", kind="file", target="a.py"),
        Topic(title="B", kind="file", target="b.py"),
    ]
    add_topics(topics, project_dir)
    pop_batch(1, project_dir)

    queue = load_queue(project_dir)
    done = [t for t in queue if t.status == "done"]
    pending = [t for t in queue if t.status == "pending"]
    assert len(done) == 1
    assert len(pending) == 1

    count = requeue_topics(topics, project_dir)
    assert count == 1


def test_requeue_multiple_failed_topics(tmp_path):
    project_dir = str(tmp_path)
    topics = [
        Topic(title="A", kind="file", target="a.py"),
        Topic(title="B", kind="function", target="b.py:func"),
        Topic(title="C", kind="general", target="overview"),
    ]
    add_topics(topics, project_dir)
    batch = pop_batch(3, project_dir)

    count = requeue_topics(batch, project_dir)
    assert count == 3

    queue = load_queue(project_dir)
    assert all(t.status == "pending" for t in queue)


def test_requeue_matches_by_kind_and_target(tmp_path):
    """Only the topic matching both kind and target is requeued."""
    project_dir = str(tmp_path)
    topics = [
        Topic(title="Auth file", kind="file", target="src/auth.py"),
        Topic(title="Router file", kind="file", target="src/router.py"),
    ]
    add_topics(topics, project_dir)
    pop_batch(2, project_dir)

    auth_topic = Topic(title="Auth file", kind="file", target="src/auth.py")
    count = requeue_topics([auth_topic], project_dir)
    assert count == 1

    queue = load_queue(project_dir)
    auth = next(t for t in queue if t.target == "src/auth.py")
    router = next(t for t in queue if t.target == "src/router.py")
    assert auth.status == "pending"
    assert router.status == "done"
