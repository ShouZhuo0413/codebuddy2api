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
