# WorkBuddy 中文管理后台

在原 codebuddy2api 转换器上增加独立管理层，部署后访问 `https://你的域名/admin/`。

## 功能

- 默认概览页：可用账号、已知总积分、完成请求数、完成成功率、HTTP 成功率、失败/进行中、平均耗时及最近 100 条请求。
- 导入桌面端 `.info` / JSON 凭据，设置备注，启用、停用、删除账号。
- 浏览器授权登录国内 CodeBuddy / WorkBuddy，无需桌面端。生成官方登录链接，在浏览器完成授权后自动获取并保存账号；同一账号再次登录更新原凭据。
- 保存多个账号，默认轮流分配新请求；可切换到手动指定模式。暂停、冷却和最近确认积分耗尽的账号不参与轮转。
- 手动刷新凭据、查询当前周期剩余积分；支持单账号与批量签到，每日自动签到可配置时间（北京时间，默认 09:00）。
- 查看访问令牌到期时间。到期状态来自本地凭据，是否能刷新及调用成功以真实模型测试为准。
- 新建和撤销客户端 API Key。新密钥只显示一次，服务端仅持久化 SHA-256 摘要。
- 中文接入指南和真实模型调用测试；测试最多等待 90 秒，生成预算上限 1024 tokens，以便推理模型完成思考并输出正文。
- 独立管理密钥；12 小时 HttpOnly / Secure / SameSite=Strict 会话 cookie，CSRF 校验，登录失败限流，上传请求最大 1 MB。

添加账号默认使用浏览器授权，也可切换到文件导入。授权链接有效期 5 分钟，完成后无需粘贴回调；新账号不会替换已选择的当前账号。当前授权仅支持国内账号。原账号和 API Key 首次启动自动迁入，之后以 management/state.json 为准。

## 文件

- `admin/server.py`：后台 API、持久化存储、客户端鉴权及转换器适配。
- `admin/browser_login.py`：WorkBuddy 授权状态创建、独立 cookie 会话、轮询和令牌交换。授权会话绑定管理登录，令牌不返回浏览器；超时、取消、登出后清理。
- `admin/pool.py`：请求级凭据绑定、轮转、冷却、积分查询与每日签到。`tests/test_account_pool.py` 验证并发隔离、轮转、签到防重复、精确积分及分页。
- `admin/metrics.py`：本次进程启动以来的有界请求统计；不存储请求正文、回复或密钥。
- `admin/static/`：无外部 CDN 依赖的中文界面。
- `tests/test_admin_server.py`：鉴权、CSRF、账号生命周期、密钥撤销、重启持久化、上传限制和限流测试。
- `deploy/admin/Dockerfile`：在已部署的转换器镜像上构建管理层，不修改模型转换逻辑。

## 配置与运行

环境变量：

| 变量 | 用途 |
|---|---|
| `ADMIN_KEY` | 独立管理密钥，至少 20 字符，建议随机生成 |
| `CODEBUDDY2OPENAI_KEY` | 首次迁入的客户端 Key；初始化后从持久化状态读取密钥列表 |
| `CODEBUDDY_AUTH_DIR` | 登录凭据目录，线上 `/data/auth` |
| `MANAGEMENT_DATA_DIR` | 账号索引与密钥摘要目录，线上 `/data/management` |

生产使用一个 Uvicorn 进程。会话和账号选择缓存在进程内，不适用于直接增加多 worker。Nginx 终止 HTTPS，后端端口仅绑定服务器回环地址。管理会话重启后需重新登录。

运行入口：`python3 -m admin.server`。直接运行原 `python3 -m core.converter` 不加载管理层，也不读取新的密钥列表。

独立新环境需先构建基础镜像：

```bash
docker build -f deploy/standalone/Dockerfile -t local/codebuddy2api:f717db6 .
cp deploy/admin/env.example .env
# 编辑 .env，分别填写随机管理密钥和客户端 API Key
docker compose -f deploy/admin/docker-compose.yml up -d --build
```

配置 Nginx HTTPS 后再登录：管理会话使用 Secure Cookie，不支持通过普通 HTTP 登录。可参考 `deploy/admin/nginx.conf.example` 配置反向代理和 `/responses` 兼容入口。该文件是 server 块内部的片段，需要自行配置域名和 TLS 证书。

账号和管理数据均挂载持久化，重建容器不会丢失。不要把 `.env`、`auth/` 或 `management/` 上传到 GitHub。新环境没有凭据时，可以先登录后台再导入账号。

## 模型目录更新

在原列表上补充 `hy3`、`hy4-preview`、`kimi-k3`、`glm-5.3`、`glm-5.3-flash`、`deepseek-v4.1-flash` 和 `kimi-k2.8-preview`。API 与管理后台共用 `core/converter.py` 中的模型列表。

这些名称不保证每个账号都有调用权限。2026-09-12 的验证中，`kimi-k2.8-preview` 在上游目录可见，但两个账号调用均返回 11102（模型服务不存在）；此项只加入列表，尚未验证调用成功。其余新增模型曾完成最小调用验证，可用性随上游变化。

## 验证

```bash
python -m unittest tests.test_admin_server -v
python -m unittest tests.test_browser_login -v
python -m unittest tests.test_account_pool -v
python -m unittest tests.test_request_metrics -v
node --check admin/static/app.js
```

5 组后台测试在本机和生产镜像内均通过。浏览器验证本地登录、账号列表和 JSON 导入；公网验证登录页、管理会话、现有客户端 Key、新 Key 创建与撤销。后台通过 deepseek-v4-flash 发起真实调用，返回 HTTP 200 / OK。

## 数据与恢复

删除账号从状态索引移除，并在 `management/trash/` 保留恢复副本；为保护正在进行的令牌刷新，原凭据文件也保留，但不会再次被状态索引选择。不提供网页回收站。需要永久清理时另行处理。

完整账号令牌不返回前端，测试记录不保存消息和回复。除新增 Key 的一次性响应外，完整客户端 Key 不可查看。管理密钥与客户端 Key 用途不同。

## 浏览器授权

1. 管理后台 → 添加账号 → 浏览器登录 → 生成登录链接。
2. 点击“打开官方登录页面”，自行完成扫码或账号登录。
3. 保持添加账号窗口打开，服务器每隔约 3 秒检查授权结果。成功后账号自动出现在列表。

该流程基于腾讯 CodeBuddy `/v2/plugin/auth/state`、`/v2/plugin/auth/token` 和 `/v2/plugin/login/account` 接口，不需要额外开放回调端口。协议实现参考 Sliverkiss/cpa-plugin 的浏览器授权流程，代码独立实现。上游接口变更可能影响登录。

只有已登录的管理员可以创建、轮询和取消自己的授权会话。服务端校验官方登录地址和上游 state 一致性；cookie 和 token 留在服务端。重新生成链接会取消同一管理会话此前的授权等待。服务重启会失效所有未完成的授权。

## 账号池

首次升级后默认启用轮转和北京时间 09:00 自动签到，可在页面底部修改。服务端每 5 分钟查询启用账号的积分及签到状态，页面每 30 秒刷新缓存视图；“刷新状态”主动查询上游。暂停账号不参与后台批量任务。

积分优先使用上游 Precise 小数字段和当前周期剩余额度，分页汇总所有未过期积分包。查询失败保留历史数值，10 分钟以上的快照标为待刷新；无数据不等于零积分。额度耗尽只影响分配，不删除账号。自动签到按上游当日状态避免重复，错过时间可补查，失败最多每 30 分钟再次尝试，成功日期持久化。

请求通过 ContextVar 绑定独立凭据，各账号 CredentialManager 缓存和刷新锁共享。上游 401/403 触发 5 分钟冷却，402/429 触发 30 分钟冷却。后续请求跳过冷却账号；已发送的流式请求不会重放，避免重复消费或重复工具输出。凭据准备失败时尝试另一个可用账号。没有可用账号返回 503。管理页连接测试使用手动指定的测试账号。

原始转换器直接启动（`python3 -m core.converter`）仍采用单账号行为；账号池运行入口是 `admin/server.py`，要求单进程部署。

## 概览统计口径

统计 POST `/v1/chat/completions`、`/v1/responses` 和 `/v1/messages`，包括后台模型测试，来源分别标记为 API 和后台测试。模型列表、计数接口、积分查询和其它管理操作不计入。`/responses` 经 Nginx 内部重写后归入 `/v1/responses`。

对话请求数是已结束请求总数，进行中的请求单独显示；未鉴权请求及无可用账号导致的失败也会计数。HTTP 成功率为 2xx 响应占比；完成成功率另外排除流式 error / failed / incomplete 事件、断开、异常和缺少结束标记的流。平均耗时为已结束请求的总耗时均值，包含失败请求。完成成功率不代表语义质量或回复正确率。

统计保存在内存中，重启清零，只保留最近 100 条元数据，不回填升级前记录。页面标明统计起始时间，每 30 秒刷新。
