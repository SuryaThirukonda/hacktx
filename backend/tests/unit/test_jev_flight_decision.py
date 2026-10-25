import json

import httpx
import pytest

from overwatch.config import load_config
from overwatch.decisions.engines import TypeSafeEngine
from overwatch.decisions.router import DecisionRouter
from overwatch.decisions.tasks import d1_reacquire
from overwatch.faults import FaultInjector


@pytest.mark.asyncio
async def test_jev_http_response_is_validated_into_a_bounded_flight_choice():
    cfg = load_config()
    cfg.decisions.primary = "typesafe"
    cfg.decisions.shadow = []
    cfg.secrets.typesafe_api_key = "local-test-key"
    router = DecisionRouter(cfg, FaultInjector(), "LIVE")
    engine = router.engines["typesafe"]
    assert isinstance(engine, TypeSafeEngine)

    def respond(request):
        body = json.loads(request.content)
        assert body["model"] == "jev-latest"
        assert "yaw_left_scan" in body["questions"]["search_action"]["criteria"]
        assert request.headers["authorization"] == "Bearer local-test-key"
        return httpx.Response(200, json={"model": "jev-test", "answers": {
            "search_action": {"type": "choice", "choice": "yaw_left_scan", "confidence": 0.95},
            "target_likely_occluded": {"type": "noul", "noul": 0.2},
            "urgency": {"type": "score", "score": 1., "confidence": 0.9},
        }})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        engine._client = client
        records = await router.run(d1_reacquire({"time_since_loss_s": 3., "prediction_bearing_rel_drone_deg": -45}),
                                   lambda: 1000.)
    assert records[0].engine == "typesafe" and records[0].acted
    assert records[0].selected == "yaw_left_scan"
