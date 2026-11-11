import random

import pytest

from mini_middleware.config import Config
from mini_middleware.metrics import Metrics
from mini_middleware.mock_erp import MockErp
from mini_middleware.pipeline import Pipeline
from mini_middleware.retry import RetryPolicy


@pytest.fixture
def erp():
    server = MockErp(token="test-token", slow_seconds=0.6).start()
    yield server
    server.stop()


@pytest.fixture
def config(tmp_path, erp):
    return Config(
        db_path=tmp_path / "mm.db",
        erp_url=erp.url,
        erp_token="test-token",
        erp_timeout=0.3,
        max_attempts=4,
    )


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def pipeline(config, sleeps):
    retry = RetryPolicy(
        max_attempts=4, base=0.01, cap=0.05, rng=random.Random(1), sleep=sleeps.append
    )
    return Pipeline(config, retry=retry, metrics=Metrics())
