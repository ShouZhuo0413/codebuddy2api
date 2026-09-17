# Responses API 修复

`/v1/responses` 现在实时转换上游 SSE，不再等待完整生成后才返回。缓存用量读取上游真实计数，并合并分段 usage，避免后续输出 token 统计覆盖先前缓存明细。

## 协议兼容

- SSE 包含事件名称、递增 sequence_number 和 item_id；工具与文本共用递增 output_index，最终 output 顺序与事件一致。
- 指定函数的 tool_choice 转为 Chat 格式，并透传 parallel_tool_calls。
- 上游错误返回 response.failed，非流式错误返回对应失败 HTTP 状态；长度和内容过滤中止返回 response.incomplete。
- 未传 stream 时默认返回 JSON；需要流式时显式设置 stream=true。
- 默认保留转换后的历史和工具 schema，关闭 Responses 自动摘要、截断和脱敏。恢复旧投影可设置 CODEBUDDY_LOSSY_PROJECTION=1；恢复原脱敏可设置 CODEBUDDY_RESPONSES_DESENSITIZE=1，同时原 desensitize 配置需要开启。流式请求不会在输出后自动重放。

## 会话账号绑定

管理服务优先使用 X-Session-Id、session_id、X-Conversation-Id 请求头，然后使用 prompt_cache_key、conversation_id、session_id 请求体字段；否则使用首条用户消息作为近似会话标识。同一会话优先复用可用账号，减少账号轮换对上游缓存的影响；账号不可用时重新分配，手动模式服从所选账号。

绑定按客户端 Key 和模型隔离，只持久化标识哈希、账号 ID 和过期时间。最多 4096 条，闲置 24 小时过期，随请求记录保存。原有单进程部署要求不变。请求体读取上限为 32 MiB。

首条消息变化或多层代理换渠道仍可能影响绑定。可靠绑定应传稳定会话 ID；账号绑定不保证上游缓存命中，也不创建本地响应缓存。

## 验证与部署

运行 `python -m pytest tests -q --tb=short -p no:cacheprovider`。
回归测试覆盖流式提前返回、流式/非流式接口、缓存字段变体与分段用量、工具事件顺序、错误事件、会话绑定和账号故障切换。测试使用模拟上游，未验证真实上游命中率。

更新后重启 Python 服务；Docker 部署沿用原 Compose 参数执行 `docker compose up -d --build`。保留现有账号数据及环境配置。

本次未新增 previous_response_id 历史存储、内置工具、自定义工具协议或图片转换支持。README 中旧的默认 Responses 投影描述以本说明为准。

## 长上下文空响应诊断

HTTP 200 不代表上游生成成功：上游可能返回 JSON 错误而非 SSE，或只输出推理内容，没有正文或工具调用。
现在支持读取 JSON completion/error，保留上游错误 code/message；无正文或工具调用且没有明确中止原因时返回 empty_upstream_response，避免误报为空成功。
仅推理且 finish_reason=length 时仍返回 incomplete/max_output_tokens，提示检查输出预算。内部推理不会作为正文返回。

新增长请求的模拟上游测试，覆盖 JSON 错误、JSON 正文、空流、仅推理及预算耗尽的流式/非流式路径；共 80 项测试通过。
这些修复解决错误被吞掉及 JSON 正文被丢弃的问题，不提高上游上下文限制。真实线上空响应的具体原因仍需结合上游错误确认。

## 进一步检查

- SSE 中 code/msg 形式的上游拒绝现在保留原始错误原因；无效 JSON 或非对象 chunk 返回明确失败，不再静默丢弃或抛出未处理异常。
- 传输结束时检查 [DONE] 或 finish_reason；缺少结束标记的部分输出返回 upstream_stream_interrupted，避免误报完整成功。
- previous_response_id、conversation、compaction 和 item_reference 尚无对应历史存储/解密支持，现明确拒绝，避免丢失历史后继续请求。客户端应发送显式消息和工具结果。
- 标准 reasoning.effort 转换为上游 reasoning_effort，避免客户端推理预算偏好被忽略。
