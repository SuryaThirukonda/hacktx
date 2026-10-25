"""Direct API contract checks with no runtime startup, paid keys, or persistent database.""" import os, sys, json
from pathlib import Path ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
os.environ["OVERWATCH_DB"] = ":memory:"
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from overwatch.config import Config
from overwatch.api.app import create_app
from test12.server import mount cfg = Config.model_validate(yaml.safe_load((ROOT / "config/default.yaml").read_text()))
cfg.secrets.overwatch_token = "verification-only"
cfg.decisions.primary = "rules"
app = create_app(cfg)
mount(app)
client = TestClient(app)
checks = {}
for route in ["/api/tts?text=hello", "/api/test12/state", "/api/test12/voice"]: r = client.get(route) checks[route + " unauthorized"] = r.status_code == 401
r = client.get("/api/test12/state?token=verification-only")
checks["test12 authorized"] = r.status_code == 200
r = client.post( "/api/locks/describe?token=verification-only", json={"text": "red shirt"}
)
checks["describe route reachable"] = r.status_code == 200
paths = [getattr(r, "path", "") for r in app.routes]
checks["describe before dynamic lock"] = paths.index( "/api/locks/describe"
) < paths.index("/api/locks/{role}")
try: with client.websocket_connect("/ws/test12/sim3d") as ws: ws.receive_json() checks["test12 socket unauthorized"] = False
except WebSocketDisconnect as e: checks["test12 socket unauthorized"] = e.code == 1008
usecases = client.get("/api/usecases").json()["items"]
city = next(u for u in usecases if u["id"] == "city")
checks["city launcher T12"] = [s["id"] for s in city["scenarios"]] == ["T12"]
print(json.dumps(checks, indent=2))
(ROOT / "evaluation/merge/api.json").write_text(json.dumps(checks, indent=2))
assert all(checks.values()), checks
