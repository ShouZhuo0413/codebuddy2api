import asyncio
import copy
import json

import httpx
import pytest

from core.anthropic_adapter import anthropic_request_to_chat


def image():
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'iVBORw0KGgo='}}


def history():
    return [
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Inspect'}, image()]},
        {'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': 'call1', 'name': 'screenshot', 'input': {}},
            {'type': 'tool_use', 'id': 'call2', 'name': 'read', 'input': {}}]},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'Continue'},
            {'type': 'tool_result', 'tool_use_id': 'call1', 'content': [image(), {'type': 'text', 'text': 'Screenshot'},
                {'type': 'image', 'source': {'type': 'url', 'url': 'https://example.test/image.png'}}]},
            image(),
            {'type': 'tool_result', 'tool_use_id': 'call2', 'content': 'file contents'}]},
    ]


def test_images_and_tool_results_survive_without_mutation():
    request = {'messages': history()}
    original = copy.deepcopy(request)
    messages = anthropic_request_to_chat(request)['messages']
    assert request == original
    assert [m['role'] for m in messages] == ['user', 'assistant', 'tool', 'tool', 'user']
    assert messages[0]['content'][1]['image_url']['url'] == 'data:image/png;base64,iVBORw0KGgo='
    assert [m['tool_call_id'] for m in messages[2:4]] == ['call1', 'call2']
    assert [p['type'] for p in messages[2]['content']] == ['image_url', 'text', 'image_url']
    assert messages[2]['content'][2]['image_url']['url'] == 'https://example.test/image.png'
    assert messages[3]['content'] == 'file contents'
    assert messages[4]['content'][0]['text'] == 'Continue'


@pytest.mark.parametrize('source', [{'type': 'file', 'file_id': 'file1'}, {'type': 'url', 'url': ''},
                                    {'type': 'base64', 'media_type': 'image/png'}, {'type': 'base64', 'data': 'abc'}])
def test_invalid_image_sources_are_rejected(source):
    with pytest.raises(ValueError):
        anthropic_request_to_chat({'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': source}]}]})


@pytest.mark.parametrize('stream,path', [(False, '/v1/messages'), (True, '/v1/messages'), (False, '/v1/messages/count_tokens')])
def test_endpoints_preserve_image_history(monkeypatch, stream, path):
    from core import converter
    real_client = httpx.AsyncClient
    captured = []
    def upstream(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":50,"completion_tokens":1,"total_tokens":51}}\n\ndata: [DONE]\n\n')
    monkeypatch.setattr(converter.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(upstream), **kw))
    monkeypatch.setattr(converter, '_check_auth', lambda *a: None)
    monkeypatch.setattr(converter, '_cred', lambda: type('Credential', (), {'get_headers': lambda self: {}})())
    monkeypatch.setattr(converter, '_log', lambda *a: None)
    monkeypatch.setitem(converter.CONFIG, 'desensitize', True)
    messages = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': 'history ' * 200} for i in range(600)] + history()
    async def run():
        async with real_client(transport=httpx.ASGITransport(app=converter.app), base_url='http://test') as client:
            result = await client.post(path, json={'model': 'test', 'stream': stream, 'messages': messages, 'max_tokens': 100})
            assert result.status_code == 200
            if path.endswith('count_tokens'):
                assert result.json()['input_tokens'] == 50
            elif not stream:
                assert result.json()['content'][0]['text'] == 'ok'
            else:
                assert 'message_stop' in result.text
    asyncio.run(run())
    result = captured[0]['messages']
    assert len(result) == 605
    assert result[600]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert result[602]['content'][0]['type'] == 'image_url'
    assert result[602]['tool_call_id'] == 'call1'
    assert result[603]['tool_call_id'] == 'call2'
    assert result[604]['role'] == 'user'
