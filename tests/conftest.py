from __future__ import annotations

import shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def isolated_env(tmp_path: Path):
    src_configs = Path("configs/workflows")
    dst_configs = tmp_path / "workflows"
    shutil.copytree(src_configs, dst_configs)
    db_file = tmp_path / "test_scoreme_decisions.db"

    app = create_app(config_dir=dst_configs, db_path=db_file)
    client = TestClient(app)
    return {
        "app": app,
        "client": client,
        "config_dir": dst_configs,
        "db_path": db_file,
    }
