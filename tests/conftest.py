import os

import pytest

os.environ.setdefault("PREFECT_LOGGING_LEVEL", "WARNING")


@pytest.fixture(scope="session", autouse=True)
def prefect_harness():
    from prefect.testing.utilities import prefect_test_harness

    with prefect_test_harness(server_startup_timeout=120):
        yield


@pytest.fixture
def lab(tmp_path):
    from dendrite import Lab

    lab = Lab(tmp_path / "lab")
    yield lab
    lab.db.dispose()


@pytest.fixture
def source(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("a,b\n1,2\n3,4\n5,6\n7,8\n")
    return p
