import asyncio
import json

import httpx
import pytest

from core import converter
from core.responses_adapter import ResponsesStreamConverter, responses_request_to_chat


def feed(conv, obj):
    return conv.feed_line('data: ' + json.dumps(obj))


def events(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith('data: ')]


@pytest.mark.parametrize('cache', [
    {'prompt_tokens_details': {'cached_tokens': 80}},
    {'input_tokens_details': {'cached_tokens': 80}},
    {'cache_read_input_tokens': 80},
    {'prompt_tokens_details': {'cached_tokens': 0}, 'prompt_cache_hit_tokens': 80},
    {'cached_tokens': 80},
])
def test_cache_variants_and_partial_usage(cache):
    conv = ResponsesStreamConverter()
    feed(conv, {'choices': [], 'usage': {'prompt_tokens': 100, **cache}})
    feed(conv, {'choices': [], 'usage': {'completion_tokens': 10, 'prompt_tokens_details': {'audio_tokens': 0}}})
    usage = conv.get_nonstream_response()['usage']
    assert usage['input_tokens'] == 100
    assert usage['input_tokens_details']['cached_tokens'] == 80
    assert usage['output_tokens'] == 10
    assert usage['total_tokens'] == 110


def test_interleaved_tools_and_text_have_stable_indices():
    conv = ResponsesStreamConverter()
    result = ''
    for delta in [
        {'tool_calls': [{'index': 0, 'id': 'call1', 'function': {'name': 'read', 'arguments': '{'}}]},
        {'content': 'Reading'},
        {'tool_calls': [{'index': 0, 'function': {'arguments': '}'}},
                        {'index': 1, 'id': 'call2', 'function': {'name': 'write', 'arguments': '{}'}}]},
    ]:
        result += feed(conv, {'choices': [{'delta': delta}]})
    result += conv.finish()
    parsed = events(result)
    added = [e for e in parsed if e['type'] == 'response.output_item.added']
    assert [e['output_index'] for e in added] == [0, 1, 2]
    ids = {e['output_index']: e['item']['id'] for e in added}
    for event in parsed:
        if 'item_id' in event:
            assert event['item_id'] == ids[event['output_index']]
    assert [i['id'] for i in parsed[-1]['response']['output']] == list(ids.values())
    assert [e['sequence_number'] for e in parsed] == list(range(len(parsed)))
    assert conv.finish() == ''


def test_upstream_error_cannot_become_empty_success():
    conv = ResponsesStreamConverter()
    result = feed(conv, {'error': {'message': 'limited', 'code': 429}})
    assert events(result)[0]['type'] == 'response.failed'
    assert conv.finish() == ''
    assert conv.get_nonstream_response()['status'] == 'failed'


@pytest.mark.parametrize('reason,expected', [('length', 'max_output_tokens'), ('content_filter', 'content_filter')])
def test_incomplete_output(reason, expected):
    conv = ResponsesStreamConverter()
    feed(conv, {'choices': [{'delta': {'content': 'partial'}, 'finish_reason': reason}]})
    event = events(conv.finish())[-1]
    assert event['type'] == 'response.incomplete'
    assert event['response']['status'] == 'incomplete'
    assert event['response']['incomplete_details']['reason'] == expected


def test_named_tool_choice():
    body = responses_request_to_chat({'input': 'hi', 'parallel_tool_calls': False,
                                     'tool_choice': {'type': 'function', 'name': 'read'}})
    assert body['tool_choice'] == {'type': 'function', 'function': {'name': 'read'}}
    assert body['parallel_tool_calls'] is False


def test_stream_delivers_text_before_upstream_eof(monkeypatch):
    real_client = httpx.AsyncClient
    released = False

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            assert released, 'Responses buffered the stream instead of returning first text'
            yield b'data: {"choices":[],"usage":{"prompt_tokens":100,"prompt_tokens_details":{"cached_tokens":80},"completion_tokens":1}}\n\n'
            yield b'data: [DONE]\n\n'

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream()))
    monkeypatch.setattr(converter.httpx, 'AsyncClient', lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(converter, '_log', lambda *args: None)

    async def run():
        nonlocal released
        stream = converter._stream_responses('https://upstream.test', {}, {'stream': True})
        first = await asyncio.wait_for(anext(stream), timeout=1)
        assert b'event: response.output_text.delta' in first
        released = True
        output = first + b''.join([part async for part in stream])
        final = events(output.decode())[-1]
        assert final['type'] == 'response.completed'
        assert final['response']['usage']['input_tokens_details']['cached_tokens'] == 80
    asyncio.run(run())


@pytest.mark.parametrize('stream', [False, True])
def test_endpoint_preserves_prompt_schema_and_cache(monkeypatch, stream):
    real_client = httpx.AsyncClient
    captured = []
    schema = {'type': 'object', '$defs': {'s': {'type': 'string'}}, 'properties': {'cmd': {'$ref': '#/$defs/s'}}}
    instruction = 'Keep repository conventions and preserve user data. ' * 300

    def upstream(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"prompt_cache_hit_tokens":80,"completion_tokens":1}}\n\ndata: [DONE]\n\n')

    transport = httpx.MockTransport(upstream)
    monkeypatch.setattr(converter.httpx, 'AsyncClient', lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(converter, '_check_auth', lambda *a: None)
    monkeypatch.setattr(converter, '_cred', lambda: type('Credential', (), {'get_headers': lambda self: {}})())
    monkeypatch.setattr(converter, '_log', lambda *a: None)
    monkeypatch.setitem(converter.CONFIG, 'desensitize', True)
    monkeypatch.delenv('CODEBUDDY_LOSSY_PROJECTION', raising=False)
    monkeypatch.delenv('CODEBUDDY_RESPONSES_DESENSITIZE', raising=False)

    async def run():
        async with real_client(transport=httpx.ASGITransport(app=converter.app), base_url='http://test') as client:
            body = {'input': 'hello', 'instructions': instruction,
                    'tools': [{'type': 'function', 'name': 'exec_command', 'parameters': schema}]}
            if stream:
                body['stream'] = True
            response = await client.post('/v1/responses', json=body)
            assert response.status_code == 200
            if stream:
                assert response.headers['content-type'].startswith('text/event-stream')
                result = events(response.text)[-1]['response']
            else:
                assert response.headers['content-type'].startswith('application/json')
                result = response.json()
            assert result['usage']['input_tokens_details']['cached_tokens'] == 80
            assert result['output'][0]['content'][0]['text'] == 'ok'
    asyncio.run(run())
    assert captured[0]['messages'][0]['content'] == instruction
    assert captured[0]['tools'][0]['function']['parameters'] == schema
    assert captured[0]['stream_options']['include_usage'] is True


@pytest.mark.parametrize('mode', ['http', 'network'])
def test_stream_transport_errors_are_failed(monkeypatch, mode):
    real_client = httpx.AsyncClient
    def upstream(request):
        if mode == 'network':
            raise httpx.ReadTimeout('timeout', request=request)
        return httpx.Response(429, json={'error': {'message': 'limited'}})
    transport = httpx.MockTransport(upstream)
    monkeypatch.setattr(converter.httpx, 'AsyncClient', lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(converter, '_log', lambda *a: None)
    async def run():
        output = b''.join([part async for part in converter._stream_responses('https://upstream.test', {}, {})])
        parsed = events(output.decode())
        assert [e['type'] for e in parsed] == ['response.failed']
        assert parsed[0]['response']['error']['code'] == ('429' if mode == 'http' else '502')
    asyncio.run(run())
