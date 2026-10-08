import os
import pathlib
import tempfile

# main / database をimportする前に、テスト専用のDBを指定する
_tmp = tempfile.mkdtemp(prefix="bbs-test-")
os.environ["BBS_DB_PATH"] = str(pathlib.Path(_tmp) / "test.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)
