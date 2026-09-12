"""Tests for per-deployment model selection and hot reload.

The critical case is the last class here. Before decision 6A a corrupt
deployment model degraded the scorer to heuristics-only with NO symptom: no
exception, no warning at request time, scores still in range and still
meaningless. That is the exact failure this project has now hit twice.
"""

import json
import threading
import time
from datetime import datetime, timezone

import pytest

from microguard.live.scorer import LiveScorer
from microguard.model import BotDetector
from microguard.parser import LogEntry


def _entry(ip="203.0.113.5"):
    return LogEntry(
        ip=ip, timestamp=datetime.now(timezone.utc), method="GET", url="/products",
        status=200, size=1, referer="", user_agent="Mozilla/5.0",
    )


@pytest.fixture()
def baseline(tmp_path):
    path = tmp_path / "baseline.json"
    BotDetector().save(str(path))
    return path


@pytest.fixture()
def deployment_model(tmp_path):
    """A model with visibly different weights from any baseline."""
    path = tmp_path / "prod_model.json"
    detector = BotDetector()
    for param in detector.model.parameters():
        param.data = 0.5
    detector.save(str(path))
    return path


class TestSelection:
    def test_the_baseline_is_used_when_no_deployment_model_exists(self, store, baseline, tmp_path):
        scorer = LiveScorer(
            store, model_path=baseline, deployment_id="prod", feedback_dir=tmp_path
        )
        assert scorer.model_loaded is True
        assert scorer.active_model_path == baseline

    def test_a_deployment_model_is_preferred_when_present(
        self, store, baseline, deployment_model, tmp_path
    ):
        scorer = LiveScorer(
            store, model_path=baseline, deployment_id="prod", feedback_dir=tmp_path
        )
        assert scorer.active_model_path == deployment_model

    def test_no_deployment_id_means_the_baseline_always(
        self, store, baseline, deployment_model, tmp_path
    ):
        """A deployment model must never be picked up by a process that did
        not ask for one."""
        scorer = LiveScorer(store, model_path=baseline, feedback_dir=tmp_path)
        assert scorer.active_model_path == baseline

    def test_scoring_works_through_the_deployment_model(
        self, store, baseline, deployment_model, tmp_path
    ):
        scorer = LiveScorer(
            store, model_path=baseline, deployment_id="prod", feedback_dir=tmp_path
        )
        result = scorer.score_request(_entry())
        assert result["model_loaded"] is True
        assert 0.0 <= result["model_score"] <= 1.0


class TestHotReload:
    def test_a_retrained_model_is_picked_up_without_a_restart(
        self, store, baseline, deployment_model, tmp_path
    ):
        scorer = LiveScorer(
            store, model_path=baseline, deployment_id="prod",
            feedback_dir=tmp_path, reload_interval=0.0,
        )
        before = scorer.score_request(_entry())["model_score"]

        replacement = BotDetector()
        for param in replacement.model.parameters():
            param.data = -0.5
        time.sleep(0.01)
        replacement.save(str(deployment_model))

        after = scorer.score_request(_entry(ip="203.0.113.6"))["model_score"]
        assert before != after

    def test_the_file_is_not_stat_ed_on_every_request(
        self, store, baseline, deployment_model, tmp_path, monkeypatch
    ):
        """A stat() per request is cheap but not free, on the one path that
        runs for every visitor. Cached the same few seconds the threshold is."""
        stats = []
        real_stat = type(deployment_model).stat

        def counting_stat(self, *args, **kwargs):
            stats.append(1)
            return real_stat(self, *args, **kwargs)

        scorer = LiveScorer(
            store, model_path=baseline, deployment_id="prod",
            feedback_dir=tmp_path, reload_interval=30.0,
        )
        monkeypatch.setattr(type(deployment_model), "stat", counting_stat)
        for i in range(10):
            scorer.score_request(_entry(ip=f"203.0.113.{i}"))

        assert len(stats) <= 1

    def test_a_deleted_deployment_model_falls_back_to_the_baseline(
        self, store, baseline, deployment_model, tmp_path
    ):
        """The documented rollback: delete one file."""
        scorer = LiveScorer(
            store, model_path=baseline, deployment_id="prod",
            feedback_dir=tmp_path, reload_interval=0.0,
        )
        assert scorer.active_model_path == deployment_model

        deployment_model.unlink()
        scorer.score_request(_entry())

        assert scorer.active_model_path == baseline

    def test_concurrent_requests_trigger_at_most_one_load(
        self, store, baseline, deployment_model, tmp_path
    ):
        """CheckHandler shares one LiveScorer across every request thread."""
        loads = []
        # A real interval, then expire exactly one window: with
        # reload_interval=0 every request legitimately re-checks, so there
        # would be nothing to serialize.
        scorer = LiveScorer(
            store, model_path=baseline, deployment_id="prod",
            feedback_dir=tmp_path, reload_interval=30.0,
        )
        original = scorer._load_active

        def counting_load():
            loads.append(1)
            time.sleep(0.01)
            return original()

        scorer._load_active = counting_load
        scorer._reloaded_at = 0.0
        scorer._active_mtime = None

        threads = [
            threading.Thread(target=lambda i=i: scorer.score_request(_entry(f"10.0.0.{i}")))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(loads) == 1


class TestCorruptModelIsRefusedLoudly:
    """CRITICAL. Before this, a corrupt model was silent heuristics-only."""

    def test_a_corrupt_swap_keeps_the_previous_model(
        self, store, baseline, deployment_model, tmp_path
    ):
        scorer = LiveScorer(
            store, model_path=baseline, deployment_id="prod",
            feedback_dir=tmp_path, reload_interval=0.0,
        )
        working = scorer.score_request(_entry())["model_score"]

        time.sleep(0.01)
        deployment_model.write_text("{not json", encoding="utf-8")
        after = scorer.score_request(_entry(ip="203.0.113.9"))

        assert after["model_loaded"] is True, "must not degrade to heuristics silently"
        assert after["model_score"] == working

    def test_the_refusal_is_visible(self, store, baseline, deployment_model, tmp_path, caplog):
        scorer = LiveScorer(
            store, model_path=baseline, deployment_id="prod",
            feedback_dir=tmp_path, reload_interval=0.0,
        )
        scorer.score_request(_entry())

        time.sleep(0.01)
        deployment_model.write_text("{not json", encoding="utf-8")
        scorer.score_request(_entry(ip="203.0.113.9"))

        assert scorer.model_refused is not None
        assert "refused" in caplog.text.lower()

    def test_a_model_of_the_wrong_shape_is_refused_too(
        self, store, baseline, deployment_model, tmp_path
    ):
        """Valid JSON, wrong weight count. load() raises ValueError for it,
        and the previous model has to survive that too."""
        scorer = LiveScorer(
            store, model_path=baseline, deployment_id="prod",
            feedback_dir=tmp_path, reload_interval=0.0,
        )
        working = scorer.score_request(_entry())["model_score"]

        time.sleep(0.01)
        deployment_model.write_text(
            json.dumps({"num_features": 19, "architecture": [4, 1], "weights": [0.1, 0.2]}),
            encoding="utf-8",
        )
        after = scorer.score_request(_entry(ip="203.0.113.9"))

        assert after["model_loaded"] is True
        assert after["model_score"] == working

    def test_a_repaired_model_is_accepted_again(
        self, store, baseline, deployment_model, tmp_path
    ):
        """A refusal must not be permanent -- the operator fixes it and the
        next reload takes it."""
        scorer = LiveScorer(
            store, model_path=baseline, deployment_id="prod",
            feedback_dir=tmp_path, reload_interval=0.0,
        )
        scorer.score_request(_entry())
        time.sleep(0.01)
        deployment_model.write_text("{not json", encoding="utf-8")
        scorer.score_request(_entry(ip="203.0.113.9"))
        assert scorer.model_refused is not None

        time.sleep(0.01)
        repaired = BotDetector()
        for param in repaired.model.parameters():
            param.data = -0.25
        repaired.save(str(deployment_model))
        scorer.score_request(_entry(ip="203.0.113.10"))

        assert scorer.model_refused is None


class TestDoubleCheckedLocking:
    def test_a_thread_that_waited_for_the_lock_does_not_reload(
        self, store, baseline, deployment_model, tmp_path
    ):
        """The second half of the lock.

        Several request threads can pass the interval check together and queue
        on the lock. Without re-checking inside it, each one would load the
        model again after the first already did -- turning one stat-and-read
        into eight, on the path nginx is waiting on.

        Scheduled deterministically rather than raced: the first thread is held
        inside the load until the second is provably blocked on the lock.
        """
        scorer = LiveScorer(
            store, model_path=baseline, deployment_id="prod",
            feedback_dir=tmp_path, reload_interval=30.0,
        )
        loads = []
        inside = threading.Event()
        release = threading.Event()
        original = scorer._load_active

        def blocking_load():
            loads.append(1)
            inside.set()
            release.wait(timeout=5)
            return original()

        scorer._load_active = blocking_load
        scorer._reloaded_at = 0.0
        scorer._active_mtime = None

        first = threading.Thread(target=scorer._select_model)
        first.start()
        assert inside.wait(timeout=5), "the first thread never reached the load"

        second = threading.Thread(target=scorer._select_model)
        second.start()
        # The second thread is now blocked on the lock the first one holds.
        time.sleep(0.05)
        release.set()

        first.join(timeout=5)
        second.join(timeout=5)

        assert loads == [1], "the waiting thread reloaded instead of re-checking"
