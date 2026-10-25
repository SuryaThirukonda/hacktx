import json

import httpx
import pytest

from overwatch.incident.muse import select_candidate


@pytest.mark.parametrize('tid,confidence,valid', [('drone-1', .95, True), (None, .2, True),
                                                ('invented', .99, False), ('drone-1', float('nan'), False)])
def test_meta_contract(monkeypatch, tid, confidence, valid):
    monkeypatch.setenv('MODEL_API_KEY', 'test-only')
    original = httpx.Client
    def handle(request):
        body = json.loads(request.content)
        assert str(request.url) == 'https://api.meta.ai/v1/chat/completions'
        assert body['model'] == 'muse-spark-1.3'
        content = body['messages'][0]['content']
        assert content[-1]['image_url']['url'].startswith('data:image/jpeg;base64,')
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(
            {'candidate_id': tid, 'confidence': confidence})}}]})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    if valid:
        assert select_candidate('red hoodie', {'drone-1': b'jpeg'})['candidate_id'] == tid
    else:
        with pytest.raises(ValueError): select_candidate('red hoodie', {'drone-1': b'jpeg'})
