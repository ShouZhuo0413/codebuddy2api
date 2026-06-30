#!/usr/bin/env python3
"""
test_responses_adapter.py — 验证 Responses API 适配层的转换逻辑。

直接运行：python3 test_responses_adapter.py
"""

import json
import sys
sys.path.insert(0, ".")

from responses_adapter import (
    responses_request_to_chat,
    ResponsesStreamConverter,
)
from desensitize import desensitize_body
from responses_projection import project_responses_chat_body


def test_simple_text_request():
    """测试：简单文本 input → messages 转换。"""
    req = {
        "model": "glm-5.2",
        "input": "Hello, how are you?",
        "instructions": "You are a helpful assistant.",
        "stream": True,
    }
    chat = responses_request_to_chat(req)
    assert chat["messages"][0] == {"role": "system", "content": "You are a helpful assistant."}
    assert chat["messages"][1] == {"role": "user", "content": "Hello, how are you?"}
    assert chat["model"] == "glm-5.2"
    print("✅ test_simple_text_request")


def test_array_input_request():
    """测试：数组 input（user + assistant + function_call + function_call_output）。"""
    req = {
        "model": "glm-5.2",
        "input": [
            {"role": "user", "content": "Fix the bug"},
            {"type": "message", "id": "msg_1", "role": "assistant",
             "content": [{"type": "output_text", "text": "I'll check the file."}]},
            {"type": "function_call", "id": "fc_1", "call_id": "call_123",
             "name": "shell", "arguments": '{"cmd":"cat main.py"}'},
            {"type": "function_call_output", "call_id": "call_123",
             "output": "print('hello')"},
            {"role": "user", "content": "Now fix it"},
        ],
        "instructions": "You are a coding assistant.",
    }
    chat = responses_request_to_chat(req)
    msgs = chat["messages"]

    assert msgs[0] == {"role": "system", "content": "You are a coding assistant."}
    assert msgs[1] == {"role": "user", "content": "Fix the bug"}
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["content"] == "I'll check the file."
    assert len(msgs[2]["tool_calls"]) == 1
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "shell"
    assert msgs[3]["role"] == "tool"
    assert msgs[3]["tool_call_id"] == "call_123"
    assert msgs[4] == {"role": "user", "content": "Now fix it"}
    print("✅ test_array_input_request")


def test_tools_conversion():
    """测试：Responses 扁平 tools 格式 → Chat 嵌套格式。"""
    req = {
        "model": "glm-5.2",
        "input": "test",
        "tools": [
            {"type": "function", "name": "shell",
             "description": "Run a shell command",
             "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}},
        ],
    }
    chat = responses_request_to_chat(req)
    tool = chat["tools"][0]
    assert tool["type"] == "function"
    assert "function" in tool
    assert tool["function"]["name"] == "shell"
    print("✅ test_tools_conversion")


def test_max_output_tokens():
    """测试：max_output_tokens → max_tokens。"""
    req = {"model": "glm-5.2", "input": "test", "max_output_tokens": 4096}
    chat = responses_request_to_chat(req)
    assert chat["max_tokens"] == 4096
    print("✅ test_max_output_tokens")


def test_developer_role():
    """测试：developer role → system。"""
    req = {"model": "glm-5.2", "input": [
        {"role": "developer", "content": "Be concise."},
        {"role": "user", "content": "Hi"},
    ]}
    chat = responses_request_to_chat(req)
    assert chat["messages"][0] == {"role": "system", "content": "Be concise."}
    assert chat["messages"][1] == {"role": "user", "content": "Hi"}
    print("✅ test_developer_role")


def test_typed_developer_message_request():
    """测试：typed message + developer role 也能映射为 system。"""
    req = {
        "model": "glm-5.2",
        "input": [
            {"type": "message", "role": "developer", "content": "Be concise."},
            {"type": "message", "role": "user", "content": "Hi"},
        ],
    }
    chat = responses_request_to_chat(req)
    assert chat["messages"][0] == {"role": "system", "content": "Be concise."}
    assert chat["messages"][1] == {"role": "user", "content": "Hi"}
    print("✅ test_typed_developer_message_request")


def test_desensitize_harness_user_and_tools():
    """测试：harness user 上下文会被摘要，tool 描述会脱敏，真实 user 不改。"""
    body = {
        "messages": [
            {"role": "system", "content": "Refuse exploit development."},
            {"role": "user", "content": "# AGENTS.md instructions\n<environment_context> sandbox escalation"},
            {"role": "user", "content": "please explain dos attacks"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "exec_command", "description": "Run dangerous exploit development checks."}}
        ],
    }
    out = desensitize_body(
        body,
        roles=("system", "developer"),
        desensitize_harness_user=True,
        desensitize_tools=True,
    )
    assert "​" in out["messages"][0]["content"]
    assert "Repository instructions and durable user context are provided." in out["messages"][1]["content"]
    assert "​" not in out["messages"][2]["content"]
    assert "​" in out["tools"][0]["function"]["description"]
    print("✅ test_desensitize_harness_user_and_tools")


def test_compact_harness_messages_and_strip_tool_metadata():
    """测试：Codex 注入长提示被压缩，tool 描述可直接裁掉。"""
    body = {
        "messages": [
            {"role": "system", "content": "You are a coding agent running in the Codex CLI. # How you work\nUse sandbox and escalation."},
            {"role": "system", "content": "<permissions instructions>\nFilesystem sandboxing defines which files can be read or written."},
            {"role": "user", "content": "# AGENTS.md instructions\n<environment_context> sandbox escalation"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "exec_command", "description": "Run dangerous exploit development checks.", "parameters": {"type": "object", "properties": {"cmd": {"type": "string", "description": "Shell command to execute."}}}}}
        ],
    }
    out = desensitize_body(
        body,
        roles=("system", "developer"),
        desensitize_harness_user=True,
        desensitize_tools=True,
        compact_harness=True,
        strip_tool_metadata=True,
    )
    assert len(out["messages"][0]["content"]) < 220
    assert "Codex CLI" in out["messages"][0]["content"]
    assert "sandboxing defines" not in out["messages"][1]["content"]
    assert "Repository instructions and environment context" in out["messages"][2]["content"]
    assert "description" not in out["tools"][0]["function"]
    assert "description" not in out["tools"][0]["function"]["parameters"]["properties"]["cmd"]
    print("✅ test_compact_harness_messages_and_strip_tool_metadata")


def test_no_compact_still_prunes_codex_runtime_metadata():
    """测试：保留全文模式仍会裁掉 Codex 注入的运行时元数据大段文本。"""
    body = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a coding agent running in the Codex CLI.\n\n"
                    "# How you work\nUse sandbox and escalation carefully.\n\n"
                    "<permissions instructions>\nFilesystem sandboxing defines which files can be read or written.\n"
                    "## How to request escalation\n...\n</permissions instructions>\n\n"
                    "The following deferred tools are now available via ToolSearch.\n..."
                ),
            },
            {
                "role": "user",
                "content": (
                    "# AGENTS.md instructions\n<INSTRUCTIONS>\nproject guidance\n</INSTRUCTIONS>"
                    "<environment_context>\nvery long runtime context\n</environment_context>\n"
                    "<skills_instructions>\nvery long skills metadata\n</skills_instructions>\n"
                    "test"
                ),
            },
            {"role": "user", "content": "test"},
        ],
    }
    out = desensitize_body(
        body,
        roles=("system", "developer"),
        desensitize_harness_user=True,
        compact_harness=False,
    )
    system_text = out["messages"][0]["content"]
    harness_text = out["messages"][1]["content"]
    assert "You are a coding agent running in the Codex CLI" in system_text
    assert "## Planning" not in system_text
    assert "## Task execution" not in system_text
    assert "### Final answer structure and style guidelines" not in system_text
    assert "# How you work" in system_text
    assert "Filesystem sandboxing defines" not in system_text
    assert "ToolSearch" not in system_text
    assert "Runtime permissions apply" in system_text
    assert "Runtime tool, agent, sk" in system_text
    assert "very long runtime context" not in harness_text
    assert "very long skills metadata" not in harness_text
    assert "# AGENTS.md instructions" not in harness_text
    assert "Repository instructions and durable user context are provided." in harness_text
    assert out["messages"][2]["content"] == "test"
    print("✅ test_no_compact_still_prunes_codex_runtime_metadata")


def test_responses_projection_compacts_codex_harness_and_tools():
    """测试：Codex 风格请求会在首发阶段直接投影为短 system + 极简 schema。"""
    body = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a coding agent running in the Codex CLI.\n"
                    "# AGENTS.md spec\nVery long harness instructions."
                ),
            },
            {
                "role": "system",
                "content": "Additional repo rule: always run tests after editing.",
            },
            {
                "role": "user",
                "content": "# AGENTS.md instructions\n<environment_context>\nlong context\n</environment_context>",
            },
            {"role": "user", "content": "实现该方案"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "exec_command",
                    "description": "Run a command with a long dangerous description",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cmd": {"type": "string", "description": "Shell command to execute."},
                            "yield_time_ms": {"type": "number", "description": "Wait time"},
                        },
                        "required": ["cmd"],
                        "additionalProperties": False,
                    },
                    "strict": False,
                },
            }
        ],
    }
    out, stats = project_responses_chat_body(body)
    assert stats["mode"] == "aggressive"
    assert out["messages"][0]["role"] == "system"
    assert "OpenAI-compatible CLI" in out["messages"][0]["content"]
    assert all("# AGENTS.md instructions" not in msg.get("content", "") for msg in out["messages"])
    assert any("Additional repo rule" in msg.get("content", "") for msg in out["messages"])
    assert out["messages"][-1] == {"role": "user", "content": "实现该方案"}
    tool = out["tools"][0]["function"]
    assert tool["name"] == "exec_command"
    assert "description" not in tool
    assert "description" not in tool["parameters"]["properties"]["cmd"]
    assert stats["projected_tool_chars"] < stats["original_tool_chars"]
    print("✅ test_responses_projection_compacts_codex_harness_and_tools")


def test_responses_projection_preserves_recent_tool_chain_and_summarizes_history():
    """测试：较早轮次会被摘要，最近 tool 链保持完整。"""
    big_output = "Chunk ID: a1\nWall time: 0.0\nProcess exited with code 0\nOutput:\n" + "\n".join(
        f"line {i}" for i in range(40)
    )
    body = {
        "messages": [
            {"role": "system", "content": "You are a coding agent running in the Codex CLI."},
            {"role": "user", "content": "# AGENTS.md instructions\n<environment_context>ctx</environment_context>"},
            {"role": "user", "content": "先看 README"},
            {
                "role": "assistant",
                "content": "I will inspect the repository.",
                "tool_calls": [
                    {
                        "id": "call_old",
                        "type": "function",
                        "function": {"name": "exec_command", "arguments": "{\"cmd\":\"ls -la\"}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_old", "content": "Output:\nREADME.md\nsrc\n"},
            {"role": "assistant", "content": "README is present."},
            {"role": "user", "content": "现在修复 converter 的 responses 链路"},
            {
                "role": "assistant",
                "content": "I will patch the proxy and then run tests.",
                "tool_calls": [
                    {
                        "id": "call_recent",
                        "type": "function",
                        "function": {
                            "name": "exec_command",
                            "arguments": json.dumps({"cmd": "sed -n '1,200p' converter.py", "yield_time_ms": 1000}),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_recent", "content": big_output},
            {"role": "assistant", "content": "I found the endpoint and will implement projection now."},
            {"role": "user", "content": "继续，别依赖 fallback retry"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "exec_command",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cmd": {"type": "string"},
                            "yield_time_ms": {"type": "number"},
                        },
                        "required": ["cmd"],
                    },
                },
            }
        ],
    }
    out, stats = project_responses_chat_body(body)
    system_messages = [m["content"] for m in out["messages"] if m["role"] == "system"]
    assert system_messages[0].startswith("You are a coding assistant serving an OpenAI-compatible CLI.")
    assert any("Earlier conversation summary" in text for text in system_messages)
    assert any("先看 README" in text for text in system_messages)

    recent_assistant = next(
        msg for msg in out["messages"]
        if msg.get("role") == "assistant" and any(tc.get("id") == "call_recent" for tc in msg.get("tool_calls", []))
    )
    recent_tool = next(msg for msg in out["messages"] if msg.get("role") == "tool" and msg.get("tool_call_id") == "call_recent")
    assert recent_assistant["tool_calls"][0]["function"]["name"] == "exec_command"
    assert "Process exited with code 0" in recent_tool["content"]
    assert "line 39" in recent_tool["content"]
    assert len(recent_tool["content"]) < len(big_output)
    assert out["messages"][-1] == {"role": "user", "content": "继续，别依赖 fallback retry"}
    assert stats["summarized_history_messages"] >= 1
    print("✅ test_responses_projection_preserves_recent_tool_chain_and_summarizes_history")


def test_responses_projection_shrinks_large_tool_arguments():
    """测试：超长 tool arguments 会压缩成结构化 JSON 摘要。"""
    long_cmd = "echo " + ("x" * 1600)
    body = {
        "messages": [
            {"role": "system", "content": "You are a coding agent running in the Codex CLI."},
            {"role": "user", "content": "执行一个很长的命令"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_long",
                        "type": "function",
                        "function": {
                            "name": "exec_command",
                            "arguments": json.dumps({"cmd": long_cmd, "yield_time_ms": 1000, "workdir": "/tmp"}),
                        },
                    }
                ],
            },
        ],
        "tools": [],
    }
    out, _ = project_responses_chat_body(body)
    args = out["messages"][-1]["tool_calls"][0]["function"]["arguments"]
    parsed = json.loads(args)
    assert parsed["cmd"].startswith("echo ")
    assert "truncated" in parsed["cmd"]
    print("✅ test_responses_projection_shrinks_large_tool_arguments")


def test_stream_converter_text():
    """测试：Chat SSE 文本流 → Responses 事件流。"""
    conv = ResponsesStreamConverter(model="glm-5.2")

    # 模拟 Chat SSE chunks
    chunks = [
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}',
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]}',
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}',
        'data: [DONE]',
    ]

    all_events = []
    for line in chunks:
        result = conv.feed_line(line)
        if result:
            for evt_line in result.strip().split("\n\n"):
                if evt_line.startswith("data: "):
                    all_events.append(json.loads(evt_line[6:]))

    # 收尾
    finish = conv.finish()
    for evt_line in finish.strip().split("\n\n"):
        if evt_line.startswith("data: "):
            all_events.append(json.loads(evt_line[6:]))

    # 验证事件类型序列
    types = [e["type"] for e in all_events]
    assert "response.created" in types
    assert "response.in_progress" in types
    assert "response.output_item.added" in types
    assert "response.content_part.added" in types
    assert "response.output_text.delta" in types
    assert "response.output_text.done" in types
    assert "response.content_part.done" in types
    assert "response.output_item.done" in types
    assert "response.completed" in types

    # 验证最终文本
    text_done = [e for e in all_events if e["type"] == "response.output_text.done"][0]
    assert text_done["text"] == "Hello world"

    # 验证 completed response
    completed = [e for e in all_events if e["type"] == "response.completed"][0]
    resp = completed["response"]
    assert resp["status"] == "completed"
    assert resp["output"][0]["type"] == "message"
    assert resp["output"][0]["content"][0]["text"] == "Hello world"
    assert resp["usage"]["input_tokens"] == 10

    print("✅ test_stream_converter_text")


def test_stream_converter_function_call():
    """测试：Chat SSE tool_calls → Responses function_call 事件。"""
    conv = ResponsesStreamConverter(model="glm-5.2")

    chunks = [
        'data: {"id":"chatcmpl-2","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_abc","type":"function","function":{"name":"shell","arguments":""}}]},"finish_reason":null}]}',
        'data: {"id":"chatcmpl-2","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"cmd"}}]},"finish_reason":null}]}',
        'data: {"id":"chatcmpl-2","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\": \\"ls\\"}"}}]},"finish_reason":null}]}',
        'data: {"id":"chatcmpl-2","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
        'data: [DONE]',
    ]

    all_events = []
    for line in chunks:
        result = conv.feed_line(line)
        if result:
            for evt_line in result.strip().split("\n\n"):
                if evt_line.startswith("data: "):
                    all_events.append(json.loads(evt_line[6:]))

    finish = conv.finish()
    for evt_line in finish.strip().split("\n\n"):
        if evt_line.startswith("data: "):
            all_events.append(json.loads(evt_line[6:]))

    types = [e["type"] for e in all_events]
    assert "response.output_item.added" in types
    assert "response.function_call_arguments.delta" in types
    assert "response.function_call_arguments.done" in types
    assert "response.completed" in types

    # 验证 function call arguments
    args_done = [e for e in all_events if e["type"] == "response.function_call_arguments.done"][0]
    assert args_done["arguments"] == '{"cmd": "ls"}'

    print("✅ test_stream_converter_function_call")


def test_nonstream_response():
    """测试：非流式 Response 对象生成。"""
    conv = ResponsesStreamConverter(model="glm-5.2")
    conv.feed_line('data: {"id":"c1","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}')
    conv.feed_line('data: {"id":"c1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":1,"total_tokens":6}}')

    resp = conv.get_nonstream_response()
    assert resp["object"] == "response"
    assert resp["status"] == "completed"
    assert resp["output"][0]["type"] == "message"
    assert resp["output"][0]["content"][0]["text"] == "Hi"
    assert resp["usage"]["input_tokens"] == 5

    print("✅ test_nonstream_response")


if __name__ == "__main__":
    test_simple_text_request()
    test_array_input_request()
    test_tools_conversion()
    test_max_output_tokens()
    test_developer_role()
    test_typed_developer_message_request()
    test_desensitize_harness_user_and_tools()
    test_compact_harness_messages_and_strip_tool_metadata()
    test_no_compact_still_prunes_codex_runtime_metadata()
    test_responses_projection_compacts_codex_harness_and_tools()
    test_responses_projection_preserves_recent_tool_chain_and_summarizes_history()
    test_responses_projection_shrinks_large_tool_arguments()
    test_stream_converter_text()
    test_stream_converter_function_call()
    test_nonstream_response()
    print(f"\n🎉 All {15} tests passed!")
