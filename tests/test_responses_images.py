import asyncio
import copy
import json

import httpx
import pytest

from core.responses_adapter import responses_request_to_chat


IMAGE = 'data:image/png;base64,iVBORw0KGgo='


def history():
    return [
        {'role': 'user', 'content': [{'type': 'input_text', 'text': 'Inspect'},
                                   {'type': 'input_image', 'image_url': IMAGE, 'detail': 'high'}]},
        {'type': 'function_call', 'name': 'screenshot', 'call_id': 'call_image', 'arguments': '{}'},
        {'type': 'function_call_output', 'call_id': 'call_image', 'output': [
            {'type': 'input_image', 'image_url': IMAGE},
            {'type': 'input_text', 'text': 'Screenshot result'},
            {'type': 'input_image', 'image_url': 'https://example.test/image.png', 'detail': 'low'},
        ]},
        {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Seen.'}]},
        {'role': 'user', 'content': 'Continue using the screenshot.'},
    ]


def test_message_and_tool_images_preserve_text_order_and_ids():
    body = {'input': history()}
    before = copy.deepcopy(body)
    messages = responses_request_to_chat(body)['messages']
    assert body == before
    assert [m['role'] for m in messages] == ['user', 'assistant', 'tool', 'assistant', 'user']
    assert messages[0]['content'] == [{'type': 'text', 'text': 'Inspect'},
                                    {'type': 'image_url', 'image_url': {'url': IMAGE, 'detail': 'high'}}]
    assert messages[1]['tool_calls'][0]['id'] == messages[2]['tool_call_id'] == 'call_image'
    assert messages[2]['content'] == [
        {'type': 'image_url', 'image_url': {'url': IMAGE}},
        {'type': 'text', 'text': 'Screenshot result'},
        {'type': 'image_url', 'image_url': {'url': 'https://example.test/image.png', 'detail': 'low'}},
    ]
    assert messages[3]['content'] == 'Seen.'


def test_existing_chat_image_and_plain_text_are_preserved():
    image = {'type': 'image_url', 'image_url': {'url': IMAGE, 'detail': 'auto'}}
    body = {'input': [{'role': 'user', 'content': [image]},
                      {'role': 'user', 'content': [{'type': 'input_text', 'text': 'a'}, {'type': 'text', 'text': 'b'}]}]}
    result = responses_request_to_chat(body)['messages']
    assert result[0]['content'] == [image]
    assert result[1]['content'] == 'ab'


@pytest.mark.parametrize('block', [
    {'type': 'input_image', 'file_id': 'file_123'},
    {'type': 'input_image', 'image_url': ''},
    {'type': 'input_image', 'image_url': {}},
    {'type': 'input_file', 'file_id': 'file_123'},
])
def test_unsupported_content_fails_instead_of_silently_losing_it(block):
    with pytest.raises(ValueError):
        responses_request_to_chat({'input': [{'type': 'function_call_output', 'call_id': 'call1', 'output': [block]}]})


@pytest.mark.parametrize('stream', [False, True])
def test_long_image_history_reaches_upstream_as_chat_content(monkeypatch, stream):
    from core import converter
    real_client = httpx.AsyncClient
    captured = []
    def upstream(request):
        body = json.loads(request.content)
        captured.append(body)
        for message in body['messages']:
            if isinstance(message.get('content'), list):
                assert all(part['type'] in ('text', 'image_url') for part in message['content'])
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    monkeypatch.setattr(converter.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(upstream), **kw))
    monkeypatch.setattr(converter, '_check_auth', lambda *a: None)
    monkeypatch.setattr(converter, '_cred', lambda: type('Credential', (), {'get_headers': lambda self: {}})())
    monkeypatch.setattr(converter, '_log', lambda *a: None)
    monkeypatch.delenv('CODEBUDDY_LOSSY_PROJECTION', raising=False)
    monkeypatch.delenv('CODEBUDDY_RESPONSES_DESENSITIZE', raising=False)
    long_history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': 'history ' * 200} for i in range(600)] + history()
    async def run():
        async with real_client(transport=httpx.ASGITransport(app=converter.app), base_url='http://test') as client:
            response = await client.post('/v1/responses', json={'input': long_history, 'stream': stream})
            assert response.status_code == 200
            if stream:
                final = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')][-1]['response']
            else:
                final = response.json()
            assert final['output'][0]['content'][0]['text'] == 'ok'
    asyncio.run(run())
    assert len(captured[0]['messages']) == 605
    assert captured[0]['messages'][602]['content'][0]['image_url']['url'] == IMAGE
    assert captured[0]['messages'][0]['content'] == long_history[0]['content']
