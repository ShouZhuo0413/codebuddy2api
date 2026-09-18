#!/usr/bin/env python3
"""
test_anthropic_adapter.py — 验证 Anthropic API 适配层的转换逻辑。

直接运行：python3 test_anthropic_adapter.py
"""

import json
import sys
sys.path.insert(0, ".")

from core.anthropic_adapter import (
    anthropic_request_to_chat,
    AnthropicStreamConverter,
)


def test_simple_text_request():
    """测试：简单文本消息 + system 字符串。"""
    req = {
        "model": "deepseek-v4-pro",
        "max_tokens": 4096,
        "system": "You are a helpful assistant.",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
        ],
    }
    chat = anthropic_request_to_chat(req)
    msgs = chat["messages"]

    assert msgs[0] == {"role": "system", "content": "You are a helpful assistant."}
    assert msgs[1] == {"role": "user", "content": "Hello"}
    assert chat["model"] == "deepseek-v4-pro"
    assert chat["max_tokens"] == 4096
    print("✅ test_simple_text_request")


def test_system_array():
    """测试：system 为 text block 数组。"""
    req = {
        "model": "auto",
        "max_tokens": 1024,
        "system": [
            {"type": "text", "text": "You are helpful."},
            {"type": "text", "text": "Be concise."},
        ],
        "messages": [],
    }
    chat = anthropic_request_to_chat(req)
    assert chat["messages"][0]["content"] == "You are helpful.\nBe concise."
    print("✅ test_system_array")


def test_text_and_tool_use():
    """测试：assistant 消息含 text + tool_use。"""
    req = {
        "model": "deepseek-v4-pro",
        "max_tokens": 4096,
        "system": "You are a coding assistant.",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "List files"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {
                        "type": "tool_use",
                        "id": "toolu_abc",
                        "name": "Bash",
                        "input": {"cmd": "ls"},
                    },
                ],
            },
        ],
    }
    chat = anthropic_request_to_chat(req)
    msgs = chat["messages"]

    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["content"] == "Let me check."
    assert len(msgs[2]["tool_calls"]) == 1
    assert msgs[2]["tool_calls"][0]["id"] == "toolu_abc"
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "Bash"
    assert msgs[2]["tool_calls"][0]["function"]["arguments"] == '{"cmd": "ls"}'
    print("✅ test_text_and_tool_use")


def test_tool_only_no_text():
    """测试：assistant 消息只有 tool_use，没有 text。"""
    req = {
        "model": "auto",
        "max_tokens": 4096,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "ls"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "Bash",
                        "input": {"cmd": "ls"},
                    },
                ],
            },
        ],
    }
    chat = anthropic_request_to_chat(req)
    msgs = chat["messages"]

    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] is None
    assert len(msgs[1]["tool_calls"]) == 1
    assert msgs[1]["tool_calls"][0]["id"] == "toolu_123"
    print("✅ test_tool_only_no_text")


def test_tool_result():
    """测试：tool_result → tool 角色消息。"""
    req = {
        "model": "auto",
        "max_tokens": 4096,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "What files?"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_abc",
                        "name": "Bash",
                        "input": {"cmd": "ls"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_abc",
                        "content": "file1.txt\nfile2.txt",
                    },
                ],
            },
        ],
    }
    chat = anthropic_request_to_chat(req)
    msgs = chat["messages"]

    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "toolu_abc"
    assert msgs[2]["content"] == "file1.txt\nfile2.txt"
    print("✅ test_tool_result")


def test_tool_result_with_user_text():
    """测试：同一 user 消息包含 text + tool_result。"""
    req = {
        "model": "auto",
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Continue."},
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_xyz",
                        "content": "output here",
                    },
                ],
            },
        ],
    }
    chat = anthropic_request_to_chat(req)
    msgs = chat["messages"]

    assert msgs[0]["role"] == "tool"
    assert msgs[0]["tool_call_id"] == "toolu_xyz"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "Continue."
    print("✅ test_tool_result_with_user_text")


def test_tools_conversion():
    """测试：Anthropic tools 格式 → Chat 格式。"""
    req = {
        "model": "deepseek-v4-pro",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "test"}]}],
        "tools": [
            {
                "name": "Bash",
                "description": "Run a shell command",
                "input_schema": {
                    "type": "object",
                    "properties": {"cmd": {"type": "string"}},
                },
            },
        ],
    }
    chat = anthropic_request_to_chat(req)
    tool = chat["tools"][0]

    assert tool["type"] == "function"
    assert tool["function"]["name"] == "Bash"
    assert tool["function"]["description"] == "Run a shell command"
    assert tool["function"]["parameters"]["type"] == "object"
    print("✅ test_tools_conversion")


def test_string_content():
    """测试：content 为简单字符串（不是 blocks 数组）。"""
    req = {
        "model": "auto",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ],
    }
    chat = anthropic_request_to_chat(req)
    assert chat["messages"][0] == {"role": "user", "content": "Hello"}
    assert chat["messages"][1] == {"role": "assistant", "content": "Hi there"}
    print("✅ test_string_content")


def test_stream_converter_text():
    """测试：Chat SSE 文本流 → Anthropic SSE 事件流。"""
    conv = AnthropicStreamConverter(model="deepseek-v4-pro")

    chunks = [
        'data: {"id":"c1","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}',
        'data: [DONE]',
    ]

    all_events = []
    for line in chunks:
        result = conv.feed_line(line)
        if result:
            # Anthropic SSE 格式：event: xxx\ndata: {...}\n\n
            for evt_block in result.strip().split("\n\n"):
                if not evt_block:
                    continue
                lines = evt_block.strip().split("\n")
                event_type = None
                data_json = None
                for evt_line in lines:
                    if evt_line.startswith("event: "):
                        event_type = evt_line[7:]
                    elif evt_line.startswith("data: "):
                        data_json = json.loads(evt_line[6:])
                if data_json:
                    all_events.append(data_json)

    finish = conv.finish()
    for evt_block in finish.strip().split("\n\n"):
        if not evt_block:
            continue
        lines = evt_block.strip().split("\n")
        for evt_line in lines:
            if evt_line.startswith("data: "):
                all_events.append(json.loads(evt_line[6:]))

    types = [e["type"] for e in all_events]

    # 必须包含的事件类型
    assert "message_start" in types
    assert "content_block_start" in types
    assert "content_block_delta" in types
    assert "content_block_stop" in types
    assert "message_delta" in types
    assert "message_stop" in types

    # 验证 content_block_start 的 type=text
    cbs = [e for e in all_events if e["type"] == "content_block_start"][0]
    assert cbs["content_block"]["type"] == "text"

    # 验证 text_delta
    deltas = [e for e in all_events if e["type"] == "content_block_delta"]
    assert len(deltas) >= 2
    assert deltas[0]["delta"]["type"] == "text_delta"

    # 验证 stop_reason
    md = [e for e in all_events if e["type"] == "message_delta"][0]
    assert md["delta"]["stop_reason"] == "end_turn"

    print("✅ test_stream_converter_text")


def test_stream_converter_tool_use():
    """测试：Chat SSE tool_calls → Anthropic tool_use 事件。"""
    conv = AnthropicStreamConverter(model="deepseek-v4-pro")

    chunks = [
        'data: {"id":"c2","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_abc","type":"function","function":{"name":"Bash","arguments":""}}]},"finish_reason":null}]}',
        'data: {"id":"c2","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"cmd"}}]},"finish_reason":null}]}',
        'data: {"id":"c2","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\": \\"ls\\"}"}}]},"finish_reason":null}]}',
        'data: {"id":"c2","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
        'data: [DONE]',
    ]

    all_events = []
    for line in chunks:
        result = conv.feed_line(line)
        if result:
            for evt_block in result.strip().split("\n\n"):
                if not evt_block:
                    continue
                lines = evt_block.strip().split("\n")
                for evt_line in lines:
                    if evt_line.startswith("data: "):
                        all_events.append(json.loads(evt_line[6:]))

    finish = conv.finish()
    for evt_block in finish.strip().split("\n\n"):
        if not evt_block:
            continue
        lines = evt_block.strip().split("\n")
        for evt_line in lines:
            if evt_line.startswith("data: "):
                all_events.append(json.loads(evt_line[6:]))

    types = [e["type"] for e in all_events]

    assert "content_block_start" in types
    assert "content_block_delta" in types
    assert "content_block_stop" in types
    assert "message_delta" in types
    assert "message_stop" in types

    # 验证 tool_use content_block
    cbs = [e for e in all_events if e["type"] == "content_block_start"][0]
    assert cbs["content_block"]["type"] == "tool_use"
    assert cbs["content_block"]["name"] == "Bash"

    # 验证 input_json_delta
    deltas = [e for e in all_events if e["type"] == "content_block_delta"]
    for d in deltas:
        assert d["delta"]["type"] == "input_json_delta"

    # 验证 stop_reason
    md = [e for e in all_events if e["type"] == "message_delta"][0]
    assert md["delta"]["stop_reason"] == "tool_use"

    print("✅ test_stream_converter_tool_use")


def test_nonstream_response():
    """测试：非流式响应对象生成。"""
    conv = AnthropicStreamConverter(model="deepseek-v4-pro")
    conv.feed_line('data: {"id":"c1","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}')
    conv.feed_line('data: {"id":"c1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":1,"total_tokens":6}}')

    resp = conv.get_nonstream_response()
    assert resp["type"] == "message"
    assert resp["role"] == "assistant"
    assert resp["model"] == "deepseek-v4-pro"
    assert resp["stop_reason"] == "end_turn"
    assert len(resp["content"]) == 1
    assert resp["content"][0]["type"] == "text"
    assert resp["content"][0]["text"] == "Hi"
    assert resp["usage"]["input_tokens"] == 5
    assert resp["usage"]["output_tokens"] == 1

    print("✅ test_nonstream_response")


def test_nonstream_response_tool_use():
    """测试：非流式响应含 tool_use。"""
    conv = AnthropicStreamConverter(model="deepseek-v4-pro")
    conv.feed_line('data: {"id":"c2","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_abc","type":"function","function":{"name":"Bash","arguments":"{\\"cmd\\": \\"ls\\"}"}}]}}]}')
    conv.feed_line('data: {"id":"c2","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}')

    resp = conv.get_nonstream_response()
    assert resp["stop_reason"] == "tool_use"
    assert len(resp["content"]) == 1
    assert resp["content"][0]["type"] == "tool_use"
    assert resp["content"][0]["name"] == "Bash"
    assert resp["content"][0]["input"] == {"cmd": "ls"}

    print("✅ test_nonstream_response_tool_use")


def test_empty_messages():
    """测试：无 messages 的请求。"""
    req = {
        "model": "auto",
        "max_tokens": 1024,
        "system": "You are helpful.",
        "messages": [],
    }
    chat = anthropic_request_to_chat(req)
    assert len(chat["messages"]) == 1
    assert chat["messages"][0]["role"] == "system"
    print("✅ test_empty_messages")


if __name__ == "__main__":
    test_simple_text_request()
    test_system_array()
    test_text_and_tool_use()
    test_tool_only_no_text()
    test_tool_result()
    test_tool_result_with_user_text()
    test_tools_conversion()
    test_string_content()
    test_stream_converter_text()
    test_stream_converter_tool_use()
    test_nonstream_response()
    test_nonstream_response_tool_use()
    test_empty_messages()
    print(f"\n🎉 All {13} tests passed!")
