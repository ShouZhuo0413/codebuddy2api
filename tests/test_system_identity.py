import copy

import pytest

from core.system_identity import filter_system_identity, filter_system_text


@pytest.mark.parametrize('identity', [
    'You are ZCode, an interactive coding agent.',
    'You are a coding agent running in the Codex CLI.',
    'You are Claude Code, an AI assistant.',
    'Your designated identity is Sisyphus.',
    '- You are powered by the model named deepseek-v4.1-flash.',
    '你是一个编程助手。',
])
def test_identity_removed_without_losing_following_rules(identity):
    text = identity + '\nFollow repository rules. Never expose credentials.\n'
    assert filter_system_text(text) == 'Follow repository rules. Never expose credentials.\n'


def test_same_line_rules_survive():
    assert filter_system_text('You are ZCode. Keep user data private.') == 'Keep user data private.'


def test_user_history_tools_and_original_are_untouched():
    text = '# AGENTS.md instructions\nYou are Claude Code.\nMain branch (you will usually use this for PRs): main\n'
    body = {'messages': [
        {'role': 'system', 'content': text},
        {'role': 'developer', 'content': [{'type': 'input_text', 'text': 'You are ZCode.'}, {'type': 'text', 'text': 'Keep rules.'}]},
        *[{'role': role, 'content': text} for role in ('user', 'assistant', 'tool')],
    ], 'tools': [{'type': 'function', 'function': {'name': 'read', 'description': text}}]}
    original = copy.deepcopy(body)
    result = filter_system_identity(body)
    assert body == original
    assert result['messages'][0]['content'] == '# AGENTS.md instructions\nMain branch: main\n'
    assert result['messages'][1]['content'] == [{'type': 'text', 'text': 'Keep rules.'}]
    assert result['messages'][2:] == original['messages'][2:]
    assert result['tools'] == original['tools']


def test_brand_mentions_are_not_identity_declarations():
    text = 'Use the OpenAI SDK.\nDo not impersonate Claude Code.\nThe user uses ZCode.\nYou are required to protect model credentials.\n'
    assert filter_system_text(text) == text


def test_identity_only_system_is_removed():
    assert filter_system_identity({'messages': [{'role': 'system', 'content': 'You are ZCode.'},
                                               {'role': 'user', 'content': 'hello'}]})['messages'] == [{'role': 'user', 'content': 'hello'}]


@pytest.mark.parametrize('path', ['/v1/chat/completions', '/v1/responses', '/v1/messages'])
def test_routes_filter_system_but_preserve_harness_user(monkeypatch, path):
    import asyncio
    import json
    import httpx
    from core import converter

    original_client = httpx.AsyncClient
    captured = []
    def upstream(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    monkeypatch.setattr(converter.httpx, 'AsyncClient', lambda **kw: original_client(transport=httpx.MockTransport(upstream), **kw))
    monkeypatch.setattr(converter, '_check_auth', lambda *a: None)
    monkeypatch.setattr(converter, '_cred', lambda: type('Credential', (), {'get_headers': lambda self: {}})())
    monkeypatch.setattr(converter, '_log', lambda *a: None)
    monkeypatch.setitem(converter.CONFIG, 'desensitize', True)
    monkeypatch.setitem(converter.CONFIG, 'no_compact', False)
    monkeypatch.delenv('CODEBUDDY_LOSSY_PROJECTION', raising=False)
    monkeypatch.delenv('CODEBUDDY_RESPONSES_DESENSITIZE', raising=False)
    user = '<system-reminder>\nYou are Claude Code. sandbox credential testing\n</system-reminder>'
    system = 'You are ZCode.\nKeep project rules.\nMain branch (you will usually use this for PRs): main'
    body = {'model': 'test', 'stream': False}
    if path.endswith('responses'):
        body.update(instructions=system, input=user)
    elif path.endswith('messages'):
        body.update(system=system, messages=[{'role': 'user', 'content': user}], max_tokens=64)
    else:
        body['messages'] = [{'role': 'developer', 'content': system}, {'role': 'user', 'content': user}]
    async def run():
        async with original_client(transport=httpx.ASGITransport(app=converter.app), base_url='http://test') as client:
            response = await client.post(path, json=body)
            assert response.status_code == 200
    asyncio.run(run())
    messages = captured[0]['messages']
    assert [m['content'] for m in messages if m['role'] == 'user'] == [user]
    system_text = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
    assert 'ZCode' not in system_text
    assert 'Main branch: main' in system_text
    assert 'Keep project rules.' in system_text
