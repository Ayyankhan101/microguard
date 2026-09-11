"""End-to-end tests for the dashboard HTTP API.

These drive the real FastAPI app through TestClient — no mocking of the
scan/model engine, so a shape change in scan_logfile() or model.json shows up
here rather than in the browser.
"""

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient


@pytest.fixture
def client():
    from microguard.dashboard.app import create_app
    return TestClient(create_app())


class TestHealth:
    def test_reports_version_and_model_status(self, client):
        response = client.get("/api/health")

        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "2.0.0"
        assert isinstance(body["model_loaded"], bool)
        assert isinstance(body["redis_connected"], bool)


class TestScanSamples:
    def test_lists_bundled_sample_logs(self, client):
        response = client.get("/api/scan/samples")

        assert response.status_code == 200
        names = [s["name"] for s in response.json()["samples"]]
        assert "sample_access.log" in names

    def test_reports_line_count_for_each_sample(self, client):
        samples = client.get("/api/scan/samples").json()["samples"]

        sample = next(s for s in samples if s["name"] == "sample_access.log")
        assert sample["lines"] == 20


class TestScan:
    def test_scanning_a_bundled_sample_returns_the_scan_logfile_dict(self, client):
        from microguard.cli import scan_logfile

        response = client.post("/api/scan", json={"sample": "sample_access.log"})

        assert response.status_code == 200
        expected = scan_logfile("data/sample_access.log")
        assert response.json() == expected

    def test_threshold_is_passed_through_to_the_engine(self, client):
        response = client.post(
            "/api/scan", json={"sample": "sample_access.log", "threshold": 0.99}
        )

        assert response.json()["threshold"] == 0.99

    def test_unknown_sample_is_rejected(self, client):
        response = client.post("/api/scan", json={"sample": "nope.log"})

        assert response.status_code == 404
        assert response.json()["detail"] == "No such sample: nope.log"

    def test_sample_name_cannot_escape_the_data_directory(self, client):
        response = client.post("/api/scan", json={"sample": "../setup.py"})

        assert response.status_code == 404
        assert response.json()["detail"] == "No such sample: ../setup.py"

    def test_unknown_request_key_is_rejected(self, client):
        response = client.post(
            "/api/scan", json={"sample": "sample_access.log", "treshold": 0.5}
        )

        assert response.status_code == 422


class TestScanUpload:
    def test_uploaded_log_is_scanned(self, client):
        with open("data/sample_access.log", "rb") as handle:
            response = client.post(
                "/api/scan/upload", files={"file": ("access.log", handle, "text/plain")}
            )

        assert response.status_code == 200
        assert response.json()["total_sessions"] > 0

    def test_upload_over_the_size_cap_is_rejected(self, client, monkeypatch):
        from microguard.dashboard import api_scan

        monkeypatch.setattr(api_scan, "MAX_UPLOAD_BYTES", 10)

        response = client.post(
            "/api/scan/upload", files={"file": ("big.log", b"x" * 100, "text/plain")}
        )

        assert response.status_code == 413

    def test_upload_leaves_no_temp_file_behind(self, client, tmp_path, monkeypatch):
        import tempfile

        # tempfile caches gettempdir(), so setting TMPDIR here would do nothing.
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

        client.post("/api/scan/upload", files={"file": ("a.log", b"garbage\n", "text/plain")})

        assert list(tmp_path.iterdir()) == []

    def test_rejected_oversize_upload_leaves_no_temp_file_behind(
        self, client, tmp_path, monkeypatch
    ):
        import tempfile

        from microguard.dashboard import api_scan

        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        monkeypatch.setattr(api_scan, "MAX_UPLOAD_BYTES", 10)

        client.post("/api/scan/upload", files={"file": ("big.log", b"x" * 100, "text/plain")})

        assert list(tmp_path.iterdir()) == []


class TestScanExport:
    def test_nginx_export_matches_the_report_formatter(self, client):
        from microguard.report import format_nginx_denylist

        results = client.post("/api/scan", json={"sample": "sample_access.log"}).json()

        response = client.post(
            "/api/scan/export", json={"results": results, "format": "nginx"}
        )

        assert response.status_code == 200
        assert response.text == format_nginx_denylist(results)

    def test_html_export_is_served_as_a_download_not_a_page(self, client):
        results = client.post("/api/scan", json={"sample": "sample_access.log"}).json()

        response = client.post(
            "/api/scan/export", json={"results": results, "format": "html"}
        )

        # format_html() does no escaping (report.py:455), so it must never be
        # served as text/html from the dashboard's own origin.
        assert response.headers["content-type"].startswith("text/plain")
        assert "attachment" in response.headers["content-disposition"]

    def test_unknown_export_format_is_rejected(self, client):
        response = client.post(
            "/api/scan/export", json={"results": {}, "format": "pdf"}
        )

        assert response.status_code == 422


class TestModel:
    def test_exposes_the_trained_network_shape_and_weights(self, client):
        from microguard.features import FEATURE_NAMES

        response = client.get("/api/model")

        assert response.status_code == 200
        body = response.json()
        assert body["num_features"] == 19
        assert body["architecture"] == [4, 1]
        assert body["feature_names"] == FEATURE_NAMES
        assert len(body["weights"]) == 85

    def test_exposes_the_normalization_ranges_predict_applies(self, client):
        body = client.get("/api/model").json()

        assert len(body["normalization"]["mins"]) == 19
        assert len(body["normalization"]["maxs"]) == 19

    def test_evaluates_the_holdout_set(self, client):
        import json

        with open("data/eval_holdout.json", encoding='utf-8') as handle:
            expected_samples = json.load(handle)["n_samples"]

        response = client.post(
            "/api/model/evaluate", json={"dataset": "holdout", "threshold": 0.5}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["n_samples"] == expected_samples
        assert sum(body["confusion"].values()) == expected_samples

    def test_evaluates_the_adversarial_set(self, client):
        response = client.post(
            "/api/model/evaluate", json={"dataset": "adversarial", "threshold": 0.5}
        )

        assert response.json()["n_samples"] == 400

    def test_raising_the_threshold_cannot_increase_the_positives(self, client):
        low = client.post(
            "/api/model/evaluate", json={"dataset": "adversarial", "threshold": 0.2}
        ).json()
        high = client.post(
            "/api/model/evaluate", json={"dataset": "adversarial", "threshold": 0.9}
        ).json()

        assert high["confusion"]["tp"] <= low["confusion"]["tp"]
        assert high["confusion"]["fp"] <= low["confusion"]["fp"]

    def test_unknown_dataset_is_rejected(self, client):
        response = client.post(
            "/api/model/evaluate", json={"dataset": "training", "threshold": 0.5}
        )

        assert response.status_code == 422


class TestStaticSpa:
    """The built SPA is served from the same origin as the API."""

    @pytest.fixture
    def built_client(self, tmp_path):
        from microguard.dashboard.app import create_app

        (tmp_path / "index.html").write_text("<title>Microguard</title>", encoding='utf-8')
        (tmp_path / "app.js").write_text("console.log(1)", encoding='utf-8')
        return TestClient(create_app(static_dir=str(tmp_path)))

    def test_serves_the_index_at_the_root(self, built_client):
        response = built_client.get("/")

        assert response.status_code == 200
        assert "Microguard" in response.text

    def test_serves_built_assets(self, built_client):
        assert built_client.get("/app.js").status_code == 200

    def test_unknown_paths_fall_back_to_the_index_for_client_routing(self, built_client):
        response = built_client.get("/live")

        assert response.status_code == 200
        assert "Microguard" in response.text

    def test_unknown_api_paths_stay_404_rather_than_returning_html(self, built_client):
        response = built_client.get("/api/nope")

        assert response.status_code == 404
        assert "Microguard" not in response.text

    def test_the_api_still_answers_when_the_spa_is_mounted(self, built_client):
        assert built_client.get("/api/health").status_code == 200

    def test_a_missing_build_leaves_the_api_working(self, tmp_path):
        from microguard.dashboard.app import create_app

        client = TestClient(create_app(static_dir=str(tmp_path / "not-built")))

        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 404


class TestSharedSecret:
    """`--token` for the case where loopback binding is not an option."""

    @pytest.fixture
    def guarded(self):
        from microguard.dashboard.app import create_app

        return TestClient(create_app(token="s3cret"))

    def test_requests_without_the_token_are_refused(self, guarded):
        assert guarded.get("/api/health").status_code == 401

    def test_the_token_lets_a_request_through(self, guarded):
        response = guarded.get("/api/health", headers={"X-Microguard-Token": "s3cret"})

        assert response.status_code == 200

    def test_a_wrong_token_is_refused(self, guarded):
        response = guarded.get("/api/health", headers={"X-Microguard-Token": "guess"})

        assert response.status_code == 401

    def test_every_api_route_is_covered_not_just_health(self, guarded):
        assert guarded.get("/api/live/stats").status_code == 401

    def test_no_token_configured_means_no_gate(self, client):
        assert client.get("/api/health").status_code == 200


class TestApiPathGuard:
    """The SPA fallback must not swallow /api 404s.

    StaticFiles hands its handler an os.sep-normalized filesystem path, so a
    guard reading that argument sees "api\\nope" on Windows and misses. The
    decision has to come from the request path.
    """

    def test_recognizes_an_api_request(self):
        from microguard.dashboard.app import _is_api_path

        assert _is_api_path({"path": "/api/live/stats"}) is True

    def test_does_not_treat_a_client_route_as_api(self):
        from microguard.dashboard.app import _is_api_path

        assert _is_api_path({"path": "/live"}) is False

    def test_does_not_match_a_path_that_merely_contains_api(self):
        from microguard.dashboard.app import _is_api_path

        assert _is_api_path({"path": "/rapid/thing"}) is False

    def test_tolerates_a_scope_without_a_path(self):
        from microguard.dashboard.app import _is_api_path

        assert _is_api_path({}) is False


class TestDegradedStates:
    """What the API does when the things it reads are missing."""

    def test_the_model_endpoint_reports_503_without_a_trained_model(self, monkeypatch):
        from microguard.dashboard import api_model
        from microguard.dashboard.app import create_app

        monkeypatch.setattr(api_model.os.path, "exists", lambda _p: False)
        client = TestClient(create_app())

        response = client.get("/api/model")

        assert response.status_code == 503
        assert "No trained model" in response.json()["detail"]

    def test_evaluation_reports_503_without_a_trained_model(self, monkeypatch):
        from microguard.dashboard import api_model
        from microguard.dashboard.app import create_app

        api_model._detector.cache_clear()
        api_model._scores.cache_clear()
        monkeypatch.setattr(api_model.os.path, "exists", lambda _p: False)
        client = TestClient(create_app())

        response = client.post("/api/model/evaluate",
                               json={"dataset": "holdout", "threshold": 0.5})

        assert response.status_code == 503
        api_model._detector.cache_clear()
        api_model._scores.cache_clear()

    def test_samples_are_empty_when_the_data_directory_is_absent(self, monkeypatch):
        from microguard.dashboard import api_scan
        from microguard.dashboard.app import create_app

        monkeypatch.setattr(api_scan, "DATA_DIR", "/nonexistent/data")
        client = TestClient(create_app())

        assert client.get("/api/scan/samples").json() == {"samples": []}


class TestPathResolution:
    def test_the_directory_itself_is_not_a_valid_target(self, tmp_path):
        from microguard.dashboard.paths import resolve_within

        assert resolve_within(str(tmp_path), ".") is None

    def test_a_file_inside_resolves(self, tmp_path):
        from microguard.dashboard.paths import resolve_within

        (tmp_path / "a.log").write_text("x", encoding='utf-8')

        assert resolve_within(str(tmp_path), "a.log") is not None


class TestSpaFallbackEdges:
    def test_a_missing_index_does_not_mask_a_real_404(self, tmp_path):
        """With no index.html there is nothing to fall back to, so the
        StaticFiles 404 must surface unchanged."""
        from microguard.dashboard.app import create_app

        (tmp_path / "app.js").write_text("console.log(1)", encoding='utf-8')
        client = TestClient(create_app(static_dir=str(tmp_path)))

        assert client.get("/some/route").status_code == 404


class TestDashboardServer:
    """run_dashboard's banner and wiring, with uvicorn stubbed out."""

    def test_reports_its_configuration(self, monkeypatch, capsys):
        import uvicorn

        from microguard.dashboard import server

        started = {}
        monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(kw))

        server.run_dashboard(port=8599, redis_url="redis://127.0.0.1:6390")

        output = capsys.readouterr().out
        assert "8599" in output
        assert "redis://127.0.0.1:6390" in output
        assert started["port"] == 8599

    def test_warns_when_bound_off_loopback_without_a_token(self, monkeypatch, capsys):
        import uvicorn

        from microguard.dashboard import server

        monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)

        server.run_dashboard(host="0.0.0.0", redis_url="redis://127.0.0.1:6390")

        assert "WARNING" in capsys.readouterr().out

    def test_a_token_silences_the_warning(self, monkeypatch, capsys):
        import uvicorn

        from microguard.dashboard import server

        monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)

        server.run_dashboard(host="0.0.0.0", token="s3cret",
                             redis_url="redis://127.0.0.1:6390")

        output = capsys.readouterr().out
        assert "api token: required" in output
        assert "WARNING" not in output

    def test_says_when_the_ui_has_not_been_built(self, monkeypatch, capsys):
        import uvicorn

        from microguard.dashboard import server

        monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
        monkeypatch.setattr(server, "STATIC_DIR", "/nonexistent/static")

        server.run_dashboard(redis_url="redis://127.0.0.1:6390")

        assert "NOT BUILT" in capsys.readouterr().out


class TestWithoutTheLiveExtra:
    def test_a_missing_redis_package_degrades_to_an_in_process_recorder(
        self, monkeypatch, caplog
    ):
        """microguard/live/__init__.py raises ImportError without redis-py, so
        the dashboard must survive a base install and say what it lost."""
        import builtins
        import logging

        from microguard.dashboard.app import create_app
        from microguard.events import InMemoryDecisionRecorder

        real_import = builtins.__import__

        def no_redis(name, *args, **kwargs):
            if name == 'redis' or name.startswith('microguard.live'):
                raise ImportError("No module named 'redis'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_redis)

        with caplog.at_level(logging.WARNING):
            app = create_app(redis_url="redis://localhost:6379")

        assert isinstance(app.state.recorder, InMemoryDecisionRecorder)
        assert "redis is not installed" in caplog.text
