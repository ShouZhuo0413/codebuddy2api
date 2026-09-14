# 一键式部署镜像：自包含，无需预先构建基础镜像。
# 同时包含协议转换核心与中文管理后台，以管理后台模式启动。
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core ./core
COPY admin ./admin

EXPOSE 8787

CMD ["python3", "-m", "admin.server"]
