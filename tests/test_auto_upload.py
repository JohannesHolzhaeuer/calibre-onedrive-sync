"""Tests for the queue behind the automatic upload of newly added books."""

from __future__ import annotations

from onedrive_sync.common.auto_upload import AUTO_UPLOAD_DELAY_MS, BookUploadQueue


def test_the_delay_outlasts_calibre_writing_the_format_files():
    """calibre creates the book record before the format exists on disk."""
    assert 1000 <= AUTO_UPLOAD_DELAY_MS <= 10_000


def test_ids_are_queued_in_order_and_only_once():
    queue = BookUploadQueue()
    assert queue.add(7) is True
    assert queue.add(7) is False
    assert queue.add_many([3, "9", 7, 3]) == 2
    assert len(queue) == 3
    assert bool(queue) is True

    assert queue.take() == [7, 3, 9]
    assert len(queue) == 0
    assert bool(queue) is False
    assert queue.take() == []


def test_a_retry_may_queue_a_book_again():
    """A batch deferred behind a manual job is put back, not uploaded twice."""
    queue = BookUploadQueue()
    queue.add_many([1, 2])
    assert queue.take() == [1, 2]
    assert queue.add_many([1, 2]) == 2
    assert queue.take() == [1, 2]


def test_unusable_ids_are_ignored():
    queue = BookUploadQueue()
    assert queue.add_many([0, -4, None, "abc", 2]) == 1
    assert queue.take() == [2]


def test_clear_forgets_the_batch_without_uploading_it():
    queue = BookUploadQueue()
    queue.add_many([1, 2])
    queue.clear()
    assert len(queue) == 0
    assert queue.add(1) is True
