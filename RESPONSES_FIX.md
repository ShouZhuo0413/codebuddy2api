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

本次未新增 previous_response_id 历史存储、内置工具或自定义工具协议。README 中旧的默认 Responses 投影描述以本说明为准。

## 系统身份过滤

在 Chat、Responses、Messages 及 Messages token 计数入口，转换为 Chat 格式后过滤 system/developer 中已识别的客户端或模型身份声明。删除身份语句，保留同条消息的其他指令；仅含身份的系统消息会移除。固定客户端标题去掉品牌，主分支模板保留分支值。

此过滤器不修改 user、assistant、tool 消息和工具 schema。旧脱敏逻辑的 harness user 处理也已关闭。默认 Responses 保留模式下不裁剪用户历史；显式启用旧 CODEBUDDY_LOSSY_PROJECTION 仍可能截断消息，若需保留全文不要开启该选项。

匹配基于明确规则，不能保证识别所有语言和任意写法，也不能保证上游安全策略放行。验证包含三种接口的角色隔离、用户模板原样保留和常规指令保留；未进行真实上游调用。


## 图片历史格式修复

上游 11101 / unsupported content type: input_image 对应 Responses 图片块没有转换为 Chat 格式。
现统一转换普通消息和 function_call_output 的内容：input_image → image_url，input_text/output_text → text。
保留 URL、base64 data URL、detail、图文顺序、tool_call_id 及完整历史。纯文字内容继续输出字符串。
不删除用户图片，不修改工具参数或图片字节。仅 file_id 的图片、缺少 URL 的图片及其他未支持的内容块明确返回 400，避免静默丢失内容。

91 项本地测试通过，包括 600 条文字历史加图片消息/工具结果的流式和非流式请求。模拟上游验证协议转换，未验证具体模型是否支持视觉或工具消息中的图片，也不扩大模型上下文窗口。
更新包 workbuddy2api-image-history-fix.zip 同时包含此前的系统身份过滤，覆盖项目根目录后重启；Docker 需重新构建。包内不包含 admin/server.py，保留服务器现有日志配置；不包含账号、密钥或 data。


## Anthropic Messages 图片历史修复

/v1/messages 原先忽略 user 的 image 块，tool_result 数组也只提取文字，造成图片静默丢失。
现转换 source.type=url/base64 为 Chat image_url，保留图文顺序、URL 和 base64 数据。仅图片的用户消息也会保留。
同一 user 消息含多个 tool_result 和普通图文时，先发出全部工具结果，再发用户图文，保持 Chat 工具调用配对顺序。
不支持的图片来源和内容块返回请求转换错误，不再静默删除。/v1/messages/count_tokens 共用转换逻辑。
99 项测试通过，包含 600 条历史的三种接口路径及原有 Responses 测试；未调用真实上游，图片是否可被模型识别仍需联调。

累计更新包 workbuddy2api-messages-responses-image-fix.zip 包含系统身份过滤、Responses 图片修复和本次 Messages 修复，覆盖原项目根目录后重启。未包含账户数据、密钥、部署配置或 admin/server.py。
