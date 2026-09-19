"""Shared test configuration for the RAG suite.

Keeps MLflow from writing ``mlflow.db`` / ``mlruns/`` into the repository
working directory. ``rag/app.py`` enables LlamaIndex autologging at import
time (``test_app.py`` imports it), so any later test that runs a real
LlamaIndex-instrumented component would otherwise log traces into MLflow's
default local store, and MLflow's default artifact root is ``./mlruns``
relative to the current directory — i.e. inside the repo.

Every test therefore starts with both the tracking store *and* the
artifact location pointed at a session-scoped temporary directory.
``test_tracing.py`` still installs its own per-test store on top of this
(module-level autouse fixtures run after this one), and the next test gets
the temp default back.

``test_tracing.py`` creates its own experiment per test, which falls back to
that same default artifact root, and MLflow exports traces asynchronously
(possibly after the test that produced them has finished). The whole session
therefore also runs from a temporary working directory, so that any relative
default MLflow path can only ever resolve inside it; pending trace exports are
flushed before the original directory is restored.

Skipped silently when MLflow is not installed (the module-level
``importorskip`` guards in the MLflow-dependent test files cover that case).
"""

from __future__ import annotations

import os

import pytest

try:
    import mlflow
except ImportError:  # pragma: no cover — MLflow is App-scoped, not always installed
    mlflow = None


@pytest.fixture(scope="session")
def _mlflow_temp_store(tmp_path_factory):
    if mlflow is None:
        return None
    root = tmp_path_factory.mktemp("mlflow_default")
    tracking_uri = f"sqlite:///{root}/mlflow.db"
    mlflow.set_tracking_uri(tracking_uri)
    experiment_id = mlflow.create_experiment(
        "rag_tests_default", artifact_location=(root / "artifacts").as_uri()
    )
    return tracking_uri, experiment_id


@pytest.fixture(autouse=True)
def _isolate_mlflow_default_store(_mlflow_temp_store):
    if _mlflow_temp_store is not None:
        tracking_uri, experiment_id = _mlflow_temp_store
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_id=experiment_id)
    yield


@pytest.fixture(scope="session", autouse=True)
def _run_from_a_temp_working_directory(tmp_path_factory):
    if mlflow is None:
        yield
        return
    original = os.getcwd()
    os.chdir(tmp_path_factory.mktemp("cwd"))
    try:
        yield
    finally:
        try:
            mlflow.flush_trace_async_logging()
        finally:
            os.chdir(original)
