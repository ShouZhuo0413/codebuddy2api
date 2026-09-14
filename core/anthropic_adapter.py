"""
anthropic_adapter.py — Anthropic Messages API ↔ OpenAI Chat Completions API 适配层。

Claude Code / CC Switch 使用 Anthropic Messages API（POST /v1/messages），
而 CodeBuddy 后端只支持 OpenAI Chat Completions 协议。本模块做双向转换：
  请求：Anthropic Messages 格式 → OpenAI Chat 格式
  响应：OpenAI Chat SSE → Anthropic Messages SSE 事件流

Anthropic Messages API 参考：https://docs.anthropic.com/en/docs/messages
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

# ---------------------------------------------------------------------------
# ID 生成
# ---------------------------------------------------------------------------

def _rand_id(prefix: str = "") -> str:
    return prefix + os.urandom(12).hex()


# ---------------------------------------------------------------------------
# 请求转换：Anthropic → Chat
# ---------------------------------------------------------------------------

def anthropic_request_to_chat(body: dict) -> dict:
    """将 Anthropic Messages API 请求体转换为 OpenAI Chat Completions 请求体。

    关键映射：
      system → messages[0] role=system
      messages[].content (blocks) → content (string) / tool_calls / tool role
      tools[].input_schema → tools[].function.parameters
      metadata / thinking → 丢弃
    """
    messages: list[dict] = []

    # system → 首条 system 消息
    system = body.get("system")
    if system:
        sys_content = _extract_system_text(system)
        if sys_content:
            messages.append({"role": "system", "content": sys_content})

    # messages → 消息转换
    for m in body.get("messages", []):
        if not isinstance(m, dict):
            continue
        messages.extend(_convert_anthropic_message(m))

    chat: dict[str, Any] = {"messages": messages, "stream": True}

    # model（透传，不做映射）
    if "model" in body:
        chat["model"] = body["model"]

    # max_tokens
    if "max_tokens" in body:
        chat["max_tokens"] = body["max_tokens"]

    # tools
    tools = body.get("tools")
    if tools:
        chat["tools"] = _convert_anthropic_tools(tools)

    if "tool_choice" in body:
        tc = body["tool_choice"]
        if isinstance(tc, dict):
            chat["tool_choice"] = {"type": tc.get("type", "any"), "function": {"name": tc.get("name", "")}}
        elif isinstance(tc, str):
            chat["tool_choice"] = tc if tc in ("none", "auto", "required") else {"type": "function", "function": {"name": tc}}

    # 透传常见参数
    for key in ("temperature", "top_p", "stop", "top_k"):
        if key in body:
            chat[key] = body[key]

    return chat


def _extract_system_text(system) -> str:
    """提取 system 字段为纯文本字符串。支持 string 和 [{type:text, text:...}] 数组。"""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def _convert_anthropic_message(msg: dict) -> list[dict]:
    """将单个 Anthropic 消息转换为 OpenAI 格式的消息（可能为多条）。"""
    role = msg.get("role", "")
    content = msg.get("content")

    # 简单字符串 content
    if isinstance(content, str):
        return [{"role": role, "content": content}]

    # 空 content
    if not isinstance(content, list) or not content:
        return []

    # content blocks → 需要解析
    blocks = content

    # 检查是否包含 tool_result（role=user 时）
    if role == "user":
        result: list[dict] = []
        text_parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            bt = block.get("type", "")
            if bt == "text":
                text_parts.append(block.get("text", ""))
            elif bt == "tool_result":
                # tool_result → 独立的 tool 消息
                tc_id = block.get("tool_use_id", "")
                output = block.get("content", "")
                if isinstance(output, list):
                    output = "".join(
                        b.get("text", "") for b in output if isinstance(b, dict) and b.get("type") == "text"
                    )
                result.append({"role": "tool", "tool_call_id": tc_id, "content": output})
        if text_parts:
            result.insert(0, {"role": "user", "content": "".join(text_parts)})
        return result

    # assistant 角色
    if role == "assistant":
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            bt = block.get("type", "")
            if bt == "text":
                text_parts.append(block.get("text", ""))
            elif bt == "tool_use":
                tc = {
                    "id": block.get("id", _rand_id("call_")),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                    },
                }
                tool_calls.append(tc)
        msg_out: dict[str, Any] = {"role": "assistant"}
        if text_parts:
            msg_out["content"] = "".join(text_parts)
        else:
            msg_out["content"] = None
        if tool_calls:
            msg_out["tool_calls"] = tool_calls
        return [msg_out]

    # 其他角色：尝试提取文本
    text = _extract_blocks_text(blocks)
    return [{"role": role, "content": text}] if text else []


def _extract_blocks_text(blocks: list) -> str:
    """从 content blocks 中提取所有 text 块合并为字符串。"""
    parts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def _convert_anthropic_tools(tools: list) -> list:
    """将 Anthropic 格式的 tools 转为 OpenAI Chat 格式。

    Anthropic:  {"name": "...", "description": "...", "input_schema": {...}}
    Chat:       {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
    """
    result = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        # 已经是 Chat 格式
        if "function" in t:
            result.append(t)
            continue
        fn: dict[str, Any] = {"name": t.get("name", "")}
        if "description" in t:
            fn["description"] = t["description"]
        if "input_schema" in t:
            fn["parameters"] = t["input_schema"]
        result.append({"type": "function", "function": fn})
    return result


# ---------------------------------------------------------------------------
# 响应转换：Chat SSE → Anthropic Messages SSE
# ---------------------------------------------------------------------------

class AnthropicStreamConverter:
    """将 OpenAI Chat SSE 流实时转换为 Anthropic Messages SSE 事件流。

    用法：
      converter = AnthropicStreamConverter(model="deepseek-v4-pro")
      for line in backend_sse:
          events = converter.feed_line(line)
          if events:
              yield events.encode()
      yield converter.finish().encode()
    """

    def __init__(self, model: str = "unknown"):
        self.msg_id = _rand_id("msg_")
        self.model = model
        self.created_at = int(time.time())

        # 状态
        self._emitted_start = False

        # text 内容块
        self._text_content = ""
        self._text_block_open = False
        self._text_block_idx = 0

        # tool_use 内容块（index → {id, name, args, block_idx, open}）
        self._tool_uses: dict[int, dict] = {}
        self._next_block_idx = 0

        # 结束信息
        self._finish_reason: str | None = None
        self._usage: dict | None = None
        self._content_filter: bool = False

    # ---- 公开接口 ----

    def feed_line(self, line: str) -> str:
        """处理一行 SSE（如 'data: {...}'），返回 Anthropic SSE 事件字符串。"""
        line = line.strip()
        if not line or not line.startswith("data:"):
            return ""
        data = line[5:].strip()
        if data == "[DONE]":
            return ""
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return ""
        return self._process_chunk(chunk)

    def finish(self) -> str:
        """流结束，发出收尾事件。"""
        events: list[str] = []

        # 关闭 text 块
        if self._text_block_open:
            events.append(self._evt(
                "content_block_stop", {"index": self._text_block_idx}
            ))
            self._text_block_open = False

        # 关闭 tool_use 块
        for tc in self._tool_uses.values():
            if tc.get("open"):
                events.append(self._evt(
                    "content_block_stop", {"index": tc["block_idx"]}
                ))
                tc["open"] = False

        # stop_reason 映射
        sr = self._finish_reason or "stop"
        stop_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        stop_reason = stop_map.get(sr, "end_turn")

        # message_delta
        delta: dict[str, str | None] = {"stop_reason": stop_reason, "stop_sequence": None}
        usage = None
        if self._usage:
            u = self._usage
            usage = {
                "input_tokens": u.get("prompt_tokens", 0),
                "output_tokens": u.get("completion_tokens", 0),
            }
        events.append(self._evt("message_delta", {"delta": delta, "usage": usage}))

        # message_stop
        events.append(self._evt("message_stop", {}))

        return "".join(events)

    def get_nonstream_response(self) -> dict:
        """获取完整的非流式 Message 响应对象。"""
        content = self._build_content_blocks()
        sr = self._finish_reason or "stop"
        stop_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        stop_reason = stop_map.get(sr, "end_turn")

        resp: dict[str, Any] = {
            "id": self.msg_id,
            "type": "message",
            "role": "assistant",
            "content": content,
            "model": self.model,
            "stop_reason": stop_reason,
            "stop_sequence": None,
        }
        if self._usage:
            resp["usage"] = {
                "input_tokens": self._usage.get("prompt_tokens", 0),
                "output_tokens": self._usage.get("completion_tokens", 0),
            }
        return resp

    # ---- 内部 ----

    def _process_chunk(self, chunk: dict) -> str:
        events: list[str] = []

        if chunk.get("model"):
            self.model = chunk["model"]

        # 首次 → message_start
        if not self._emitted_start:
            events.append(self._evt("message_start", {
                "message": {
                    "id": self.msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": self.model,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                }
            }))
            self._emitted_start = True

        if chunk.get("usage"):
            self._usage = chunk["usage"]

        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            finish = choice.get("finish_reason")

            # content delta
            content = delta.get("content")
            if content:
                self._text_content += content
                if not self._text_block_open:
                    self._text_block_idx = self._next_block_idx
                    self._next_block_idx += 1
                    events.append(self._evt("content_block_start", {
                        "index": self._text_block_idx,
                        "content_block": {"type": "text", "text": ""},
                    }))
                    self._text_block_open = True
                events.append(self._evt("content_block_delta", {
                    "index": self._text_block_idx,
                    "delta": {"type": "text_delta", "text": content},
                }))

            # tool_calls delta
            for tc in delta.get("tool_calls", []):
                idx = tc.get("index", 0)
                if idx not in self._tool_uses:
                    block_idx = self._next_block_idx
                    self._next_block_idx += 1
                    self._tool_uses[idx] = {
                        "id": tc.get("id", ""),
                        "name": "",
                        "args": "",
                        "block_idx": block_idx,
                        "open": False,
                    }
                slot = self._tool_uses[idx]
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function", {})
                if fn.get("name"):
                    slot["name"] = fn["name"]

                if not slot["open"]:
                    events.append(self._evt("content_block_start", {
                        "index": slot["block_idx"],
                        "content_block": {"type": "tool_use", "id": slot["id"], "name": slot["name"], "input": {}},
                    }))
                    slot["open"] = True

                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
                    events.append(self._evt("content_block_delta", {
                        "index": slot["block_idx"],
                        "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]},
                    }))

            if finish:
                self._finish_reason = finish

                # finish_reason 出现时关闭当前打开的块
                if self._text_block_open:
                    events.append(self._evt("content_block_stop", {
                        "index": self._text_block_idx
                    }))
                    self._text_block_open = False

                for tc in self._tool_uses.values():
                    if tc.get("open"):
                        events.append(self._evt("content_block_stop", {
                            "index": tc["block_idx"]
                        }))
                        tc["open"] = False

        return "".join(events)

    def _evt(self, event_type: str, data: dict) -> str:
        """格式化一个 Anthropic SSE 事件（含 event: 行）。"""
        payload = {"type": event_type, **data}
        return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def _build_content_blocks(self) -> list[dict]:
        """构造完整的 content blocks 数组（用于非流式响应）。"""
        blocks: list[dict] = []

        # text block
        if self._text_content or self._text_block_open:
            blocks.append({"type": "text", "text": self._text_content})

        # tool_use blocks
        for _, tc in sorted(self._tool_uses.items()):
            block: dict[str, Any] = {
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["name"],
                "input": {},
            }
            # 尝试将 args 解析为 JSON object
            try:
                block["input"] = json.loads(tc["args"])
            except (json.JSONDecodeError, ValueError):
                block["input"] = tc["args"]
            blocks.append(block)

        return blocks
