ARG BASE_IMAGE=local/codebuddy2api:f717db6
FROM ${BASE_IMAGE}
WORKDIR /app
COPY core ./core
COPY admin ./admin
CMD ["python3", "-m", "admin.server"]
