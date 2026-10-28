import argparse

import uvicorn

from . import create_app

ap = argparse.ArgumentParser(prog="planner.web")
ap.add_argument("--backend", default="api", choices=["api", "local"])    # claude-code is never served (Planner.md 15)
ap.add_argument("--config", default="planner/config.toml")
a = ap.parse_args()
uvicorn.run(create_app(config=a.config, backend=a.backend), host="127.0.0.1", port=8300)    # local only
