"""
responses_adapter.py — OpenAI Responses API ↔ Chat Completions API 适配层。

Codex CLI 使用 Responses API（POST /v1/responses），而 CodeBuddy 后端只支持
Chat Completions 协议。本模块做双向转换：
  请求：Responses input/instructions/tools → Chat messages/tools
  响应：Chat SSE delta → Responses 语义事件流（response.created / output_text.delta / …）

事件类型参考：https://developers.openai.com/api/docs/guides/streaming-responses
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

# ---------------------------------------------------------------------------
# ID 生成
# ---------------------------------------------------------------------------

def _rand_id(prefix: str = "resp_") -> str:
    return prefix + os.urandom(12).hex()

# ---------------------------------------------------------------------------
# 请求转换：Responses → Chat
# ---------------------------------------------------------------------------

def responses_request_to_chat(body: dict) -> dict:
    """将 Responses API 请求体转换为 Chat Completions 请求体。

    关键映射：
      input → messages
      instructions → system message（置顶）
      max_output_tokens → max_tokens
      tools 格式微调（Responses 用 name，Chat 用 function.name）
    """
    messages: list[dict] = []

    # instructions → system message
    instructions = body.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    # input → messages
    inp = body.get("input", [])
    if isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        messages.extend(_convert_input_items(inp))

    # 构造 Chat body
    chat: dict[str, Any] = {"messages": messages, "stream": True}

    # model
    if "model" in body:
        chat["model"] = body["model"]

    # tools — Responses 和 Chat 的 function tool 格式略有不同
    tools = body.get("tools")
    if tools:
        chat["tools"] = _convert_tools_for_chat(tools)
    if "tool_choice" in body:
        choice = body["tool_choice"]
        if isinstance(choice, dict) and choice.get("type") == "function" and "name" in choice:
            choice = {"type": "function", "function": {"name": choice["name"]}}
        chat["tool_choice"] = choice
    if "parallel_tool_calls" in body:
        chat["parallel_tool_calls"] = body["parallel_tool_calls"]

    # 透传常见参数
    for key in ("temperature", "top_p", "stop", "seed",
                "presence_penalty", "frequency_penalty",
                "response_format", "reasoning_effort"):
        if key in body:
            chat[key] = body[key]

    # max_output_tokens → max_tokens
    if "max_output_tokens" in body:
        chat["max_tokens"] = body["max_output_tokens"]
    elif "max_tokens" in body:
        chat["max_tokens"] = body["max_tokens"]

    return chat


def _convert_input_items(items: list) -> list[dict]:
    """将 Responses API 的 input 数组转换为 Chat messages。

    input 里可能包含：
      - {"role": "user/developer", "content": ...}   → 直接映射
      - {"type": "message", ...}                      → 助手消息
      - {"type": "function_call", ...}                → 需合并到前面的助手消息
      - {"type": "function_call_output", ...}         → tool 角色
    """
    messages: list[dict] = []
    # 临时缓存：合并相邻的 assistant message 和 function_call
    pending_assistant_content: str | list | None = None
    pending_tool_calls: list[dict] = []

    def _flush_assistant():
        nonlocal pending_assistant_content, pending_tool_calls
        if pending_assistant_content is not None or pending_tool_calls:
            msg: dict[str, Any] = {"role": "assistant",
                                   "content": pending_assistant_content or ""}
            if pending_tool_calls:
                msg["tool_calls"] = pending_tool_calls[:]
            messages.append(msg)
            pending_assistant_content = None
            pending_tool_calls.clear()

    for item in items:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        role = item.get("role", "")

        # 简单消息 {"role": "user", "content": "..."}
        if item_type is None and role in ("user", "system", "developer"):
            _flush_assistant()
            mapped_role = "system" if role == "developer" else role
            content = _extract_content(item.get("content", ""))
            messages.append({"role": mapped_role, "content": content})
            continue

        # typed message（Responses 里常见）
        if item_type == "message" and role in ("user", "system", "developer"):
            _flush_assistant()
            mapped_role = "system" if role == "developer" else role
            content = _extract_content(item.get("content", ""))
            messages.append({"role": mapped_role, "content": content})
            continue

        # assistant 消息（来自前一轮输出）
        if item_type == "message" and role == "assistant":
            _flush_assistant()
            content_parts = item.get("content", [])
            text = _extract_content(content_parts)
            pending_assistant_content = text
            continue

        # 简单 role=assistant（无 type 标记）
        if item_type is None and role == "assistant":
            _flush_assistant()
            content = _extract_content(item.get("content", ""))
            pending_assistant_content = content
            continue

        # function_call — 合并到前面的 assistant 消息
        if item_type == "function_call":
            if pending_assistant_content is None:
                pending_assistant_content = ""
            pending_tool_calls.append({
                "id": item.get("call_id", item.get("id", _rand_id("call_"))),
                "type": "function",
                "function": {
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", "{}"),
                },
            })
            continue

        # function_call_output → tool 消息
        if item_type == "function_call_output":
            _flush_assistant()
            messages.append({
                "role": "tool",
                "tool_call_id": item.get("call_id", ""),
                "content": _extract_content(item.get("output", "")),
            })
            continue

        # 其他未知类型 — 尝试当作普通消息
        if role:
            _flush_assistant()
            content = _extract_content(item.get("content", ""))
            messages.append({"role": role, "content": content})

    _flush_assistant()
    return messages


def _extract_content(content) -> str | list:
    """Convert Responses content blocks without dropping images or text order."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        has_image = False
        for p in content:
            if isinstance(p, dict):
                kind = p.get("type")
                if kind in ("input_text", "text", "output_text"):
                    parts.append({"type": "text", "text": p.get("text", "")})
                elif kind in ("input_image", "image_url"):
                    value = p.get("image_url")
                    if isinstance(value, str):
                        image = {"url": value}
                    elif isinstance(value, dict):
                        image = dict(value)
                    else:
                        raise ValueError("Image content requires image_url (URL or data URL); file_id images are not supported.")
                    if not isinstance(image.get("url"), str) or not image["url"].strip():
                        raise ValueError("Image content requires a non-empty image_url.")
                    if "detail" in p:
                        image["detail"] = p["detail"]
                    parts.append({"type": "image_url", "image_url": image})
                    has_image = True
                else:
                    raise ValueError(f"Unsupported Responses content block: {kind!r}")
            elif isinstance(p, str):
                parts.append({"type": "text", "text": p})
            else:
                raise ValueError("Responses content blocks must be text or objects.")
        return parts if has_image else "".join(p["text"] for p in parts)
    if content is None:
        return ""
    return str(content)


def _extract_output_text(content_parts: list) -> str:
    """从 Responses output content parts 提取纯文本。"""
    texts = []
    for part in content_parts:
        if isinstance(part, dict) and part.get("type") == "output_text":
            texts.append(part.get("text", ""))
    return "".join(texts)


def _convert_tools_for_chat(tools: list) -> list:
    """将 Responses 格式的 tools 转为 Chat 格式。

    Responses:  {"type": "function", "name": "shell", "description": ..., "parameters": ...}
    Chat:       {"type": "function", "function": {"name": "shell", "description": ..., "parameters": ...}}
    """
    result = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") != "function":
            continue
        # 已经是 Chat 格式（有 "function" key）
        if "function" in t:
            result.append(t)
            continue
        # Responses 扁平格式 → Chat 嵌套格式
        fn: dict[str, Any] = {"name": t.get("name", "")}
        if "description" in t:
            fn["description"] = t["description"]
        if "parameters" in t:
            fn["parameters"] = t["parameters"]
        if "strict" in t:
            fn["strict"] = t["strict"]
        result.append({"type": "function", "function": fn})
    return result


# ---------------------------------------------------------------------------
# 响应转换：Chat → Responses
# ---------------------------------------------------------------------------

class ResponsesStreamConverter:
    """将 Chat SSE 流实时转换为 Responses API 语义事件流。

    用法：
      converter = ResponsesStreamConverter(model="glm-5.2")
      # 对后端返回的每个 SSE 行调 feed_line()
      # feed_line 返回要发送给客户端的 Responses 事件字符串（可能多行）
      for line in backend_sse:
          events = converter.feed_line(line)
          if events:
              yield events.encode()
      # 流结束后调 finish() 获取收尾事件
      yield converter.finish().encode()
    """

    def __init__(self, model: str = "unknown"):
        self.resp_id = _rand_id("resp_")
        self.msg_id = _rand_id("msg_")
        self.model = model
        self.created_at = int(time.time())

        # 状态标记
        self._emitted_created = False
        self._emitted_msg_item = False
        self._emitted_content_part = False

        # 累积内容
        self._content = ""
        self._tool_calls: dict[int, dict] = {}  # index → {id, name, args, fc_id, output_idx, emitted}
        self._finish_reason: str | None = None
        self._usage: dict | None = None
        self._sequence_number = 0
        self._next_output_index = 0
        self._msg_output_index = None
        self._error = None
        self._finished = False

    # ---- 公开接口 ----

    def feed_line(self, line: str) -> str:
        """处理一行 SSE（如 'data: {...}'），返回转换后的 Responses 事件字符串。"""
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
        """流结束后，发出收尾事件（done + completed）。"""
        if self._finished or self._error:
            return ""
        self._finished = True
        events: list[str] = []
        response = self._response_obj("completed")

        # 关闭 text content
        if self._emitted_content_part:
            events.append(self._evt("response.output_text.done", {
                "output_index": self._msg_output_index, "item_id": self.msg_id, "content_index": 0, "text": self._content
            }))
            events.append(self._evt("response.content_part.done", {
                "output_index": self._msg_output_index, "item_id": self.msg_id, "content_index": 0,
                "part": {"type": "output_text", "text": self._content, "annotations": []}
            }))

        if self._emitted_msg_item:
            events.append(self._evt("response.output_item.done", {
                "output_index": self._msg_output_index,
                "item": self._msg_item(response["status"])
            }))

        # 关闭 function calls
        for idx in sorted(self._tool_calls):
            tc = self._tool_calls[idx]
            if tc.get("emitted"):
                oi = tc["output_idx"]
                events.append(self._evt("response.function_call_arguments.done", {
                    "output_index": oi, "item_id": tc["fc_id"], "arguments": tc["args"]
                }))
                events.append(self._evt("response.output_item.done", {
                    "output_index": oi, "item": self._fc_item(tc, response["status"])
                }))

        # response.completed
        events.append(self._evt("response." + response["status"], {
            "response": response
        }))
        return "".join(events)

    def get_nonstream_response(self) -> dict:
        """流结束后获取完整的非流式 Response 对象。"""
        return self._response_obj("completed")

    # ---- 内部 ----

    def _process_chunk(self, chunk: dict) -> str:
        events: list[str] = []
        if self._error or self._finished:
            return ""
        if chunk.get("error"):
            error = chunk["error"]
            if isinstance(error, dict):
                return self.error(error.get("message", str(error)), error.get("code", "upstream_error"))
            return self.error(str(error), "upstream_error")

        # 模型名
        if chunk.get("model"):
            self.model = chunk["model"]

        # 首次 → 发 created + in_progress
        if not self._emitted_created:
            resp = self._response_obj("in_progress")
            events.append(self._evt("response.created", {"response": resp}))
            events.append(self._evt("response.in_progress", {"response": resp}))
            self._emitted_created = True

        # usage
        if chunk.get("usage"):
            if self._usage is None:
                self._usage = {}
            for key, value in chunk["usage"].items():
                if isinstance(value, dict) and isinstance(self._usage.get(key), dict):
                    self._usage[key].update(value)
                elif value is not None:
                    self._usage[key] = value

        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            finish = choice.get("finish_reason")

            # ---- content delta ----
            content = delta.get("content")
            if content:
                if not self._emitted_msg_item:
                    self._msg_output_index = self._next_output_index
                    self._next_output_index += 1
                    events.append(self._evt("response.output_item.added", {
                        "output_index": self._msg_output_index,
                        "item": self._msg_item("in_progress", empty=True)
                    }))
                    self._emitted_msg_item = True

                if not self._emitted_content_part:
                    events.append(self._evt("response.content_part.added", {
                        "output_index": self._msg_output_index, "item_id": self.msg_id, "content_index": 0,
                        "part": {"type": "output_text", "text": "", "annotations": []}
                    }))
                    self._emitted_content_part = True

                self._content += content
                events.append(self._evt("response.output_text.delta", {
                    "output_index": self._msg_output_index, "item_id": self.msg_id, "content_index": 0, "delta": content
                }))

            # ---- tool_calls delta ----
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                if idx not in self._tool_calls:
                    oi = self._next_output_index
                    self._next_output_index += 1
                    self._tool_calls[idx] = {
                        "id": tc.get("id", ""),
                        "name": "",
                        "args": "",
                        "fc_id": _rand_id("fc_"),
                        "output_idx": oi,
                        "emitted": False,
                    }
                slot = self._tool_calls[idx]
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function", {})
                if fn.get("name"):
                    slot["name"] = fn["name"]

                if not slot["emitted"]:
                    # 确保 msg item 已发出（即使 content 为空）
                    if not self._emitted_msg_item and (self._content or not self._tool_calls):
                        pass  # 不需要额外处理
                    events.append(self._evt("response.output_item.added", {
                        "output_index": slot["output_idx"],
                        "item": self._fc_item(slot, "in_progress")
                    }))
                    slot["emitted"] = True

                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
                    events.append(self._evt("response.function_call_arguments.delta", {
                        "output_index": slot["output_idx"],
                        "item_id": slot["fc_id"],
                        "delta": fn["arguments"]
                    }))

            if finish:
                self._finish_reason = finish

        return "".join(events)

    def error(self, message: str, code) -> str:
        if self._error or self._finished:
            return ""
        self._error = {"message": message, "code": str(code), "type": "upstream_error"}
        return self._evt("response.failed", {"response": self._response_obj("failed")})

    def _evt(self, event_type: str, data: dict) -> str:
        """格式化一个 SSE 事件。"""
        payload = {"type": event_type, "sequence_number": self._sequence_number, **data}
        self._sequence_number += 1
        return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def _msg_item(self, status: str = "in_progress", empty: bool = False) -> dict:
        content = [] if empty else [
            {"type": "output_text", "text": self._content, "annotations": []}
        ]
        return {
            "type": "message",
            "id": self.msg_id,
            "status": status,
            "role": "assistant",
            "content": content,
        }

    def _fc_item(self, tc: dict, status: str) -> dict:
        return {
            "type": "function_call",
            "id": tc["fc_id"],
            "call_id": tc["id"],
            "name": tc["name"],
            "arguments": tc["args"],
            "status": status,
        }

    def _response_obj(self, status: str) -> dict:
        incomplete = None
        if self._error:
            status = "failed"
        elif status == "completed" and self._finish_reason in ("length", "content_filter", "content-filter"):
            status = "incomplete"
            incomplete = {"reason": "max_output_tokens" if self._finish_reason == "length" else "content_filter"}
        output = []
        if self._emitted_msg_item or self._content:
            output.append(self._msg_item(status))
        for idx in sorted(self._tool_calls):
            tc = self._tool_calls[idx]
            if tc.get("emitted"):
                output.append(self._fc_item(tc, status))
        indices = {tc["fc_id"]: tc["output_idx"] for tc in self._tool_calls.values()}
        indices[self.msg_id] = self._msg_output_index
        output.sort(key=lambda item: indices[item["id"]])

        usage = None
        if self._usage:
            u = self._usage
            usage = {
                "input_tokens": u.get("prompt_tokens", u.get("input_tokens", 0)),
                "input_tokens_details": {"cached_tokens": _cached_tokens(u)},
                "output_tokens": u.get("completion_tokens", u.get("output_tokens", 0)),
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": u.get("total_tokens", u.get("prompt_tokens", u.get("input_tokens", 0)) + u.get("completion_tokens", u.get("output_tokens", 0))),
            }

        return {
            "id": self.resp_id,
            "object": "response",
            "created_at": self.created_at,
            "status": status,
            "error": self._error,
            "incomplete_details": incomplete,
            "model": self.model,
            "output": output,
            "parallel_tool_calls": True,
            "usage": usage,
        }


def _cached_tokens(usage: dict) -> int:
    values = [
        (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
        (usage.get("input_tokens_details") or {}).get("cached_tokens"),
        usage.get("cache_read_input_tokens"), usage.get("prompt_cache_hit_tokens"),
        usage.get("cached_tokens"),
    ]
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    return 0
