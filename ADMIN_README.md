# WorkBuddy 中文管理后台

在原 codebuddy2api 转换器上增加独立管理层，部署后访问 `https://你的域名/admin/`。

## 功能

- 导入桌面端 `.info` / JSON 凭据，设置备注，启用、停用、删除账号。
- 保存多个账号，手动选择当前账号。切换对后续请求生效，不提供自动轮询或负载均衡。
- 查看访问令牌到期时间。到期状态来自本地凭据，是否能刷新及调用成功以真实模型测试为准。
- 新建和撤销客户端 API Key。新密钥只显示一次，服务端仅持久化 SHA-256 摘要。
- 中文接入指南和真实模型调用测试；测试最多等待 90 秒，生成预算上限 1024 tokens，以便推理模型完成思考并输出正文。
- 独立管理密钥；12 小时 HttpOnly / Secure / SameSite=Strict 会话 cookie，CSRF 校验，登录失败限流，上传请求最大 1 MB。

本项目没有新增 OAuth 授权服务：需先在桌面客户端登录，再导入文件。原账号和 API Key 首次启动自动迁入，之后以 management/state.json 为准。

## 文件

- `admin_server.py`：后台 API、持久化存储、客户端鉴权及转换器适配。
- `admin_static/`：无外部 CDN 依赖的中文界面。
- `test_admin_server.py`：鉴权、CSRF、账号生命周期、密钥撤销、重启持久化、上传限制和限流测试。
- `Dockerfile.admin`：在已部署的转换器镜像上构建管理层，不修改模型转换逻辑。

## 配置与运行

环境变量：

| 变量 | 用途 |
|---|---|
| `ADMIN_KEY` | 独立管理密钥，至少 20 字符，建议随机生成 |
| `CODEBUDDY2OPENAI_KEY` | 首次迁入的客户端 Key；初始化后从持久化状态读取密钥列表 |
| `CODEBUDDY_AUTH_DIR` | 登录凭据目录，线上 `/data/auth` |
| `MANAGEMENT_DATA_DIR` | 账号索引与密钥摘要目录，线上 `/data/management` |

生产使用一个 Uvicorn 进程。会话和账号选择缓存在进程内，不适用于直接增加多 worker。Nginx 终止 HTTPS，后端端口仅绑定服务器回环地址。管理会话重启后需重新登录。

运行入口：`python3 admin_server.py`。直接运行原 `converter.py` 不加载管理层，也不读取新的密钥列表。

独立新环境需先构建基础镜像：

```bash
docker build -f Dockerfile -t local/codebuddy2api:f717db6 .
cp .env.admin.example .env
# 编辑 .env，分别填写随机管理密钥和客户端 API Key
docker compose -f docker-compose.admin.yml up -d --build
```

配置 Nginx HTTPS 后再登录：管理会话使用 Secure Cookie，不支持通过普通 HTTP 登录。可参考 `deploy/nginx-console.conf.example` 配置反向代理和 `/responses` 兼容入口。该文件是 server 块内部的片段，需要自行配置域名和 TLS 证书。

账号和管理数据均挂载持久化，重建容器不会丢失。不要把 `.env`、`auth/` 或 `management/` 上传到 GitHub。新环境没有凭据时，可以先登录后台再导入账号。

## 模型目录更新

在原列表上补充 `hy3`、`hy4-preview`、`kimi-k3`、`glm-5.3`、`glm-5.3-flash`、`deepseek-v4.1-flash` 和 `kimi-k2.8-preview`。API 与管理后台共用 `converter.py` 中的模型列表。

这些名称不保证每个账号都有调用权限。2026-09-12 的验证中，`kimi-k2.8-preview` 在上游目录可见，但两个账号调用均返回 11102（模型服务不存在）；此项只加入列表，尚未验证调用成功。其余新增模型曾完成最小调用验证，可用性随上游变化。

## 验证

```bash
python -m unittest test_admin_server -v
node --check admin_static/app.js
```

5 组后台测试在本机和生产镜像内均通过。浏览器验证本地登录、账号列表和 JSON 导入；公网验证登录页、管理会话、现有客户端 Key、新 Key 创建与撤销。后台通过 deepseek-v4-flash 发起真实调用，返回 HTTP 200 / OK。

## 数据与恢复

删除账号从状态索引移除，并在 `management/trash/` 保留恢复副本；为保护正在进行的令牌刷新，原凭据文件也保留，但不会再次被状态索引选择。不提供网页回收站。需要永久清理时另行处理。

完整账号令牌不返回前端，测试记录不保存消息和回复。除新增 Key 的一次性响应外，完整客户端 Key 不可查看。管理密钥与客户端 Key 用途不同。
