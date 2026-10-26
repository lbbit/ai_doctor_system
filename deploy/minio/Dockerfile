# MinIO 社区版自 2025 年底转为 source-only 分发：公共 registry 镜像与 dl.min.io
# 二进制均已下架（HTTP 410），官方推荐 `go install github.com/minio/minio@latest`。
# 本文件即按该方式源码构建，产出行为与原官方镜像一致（S3 API、健康端点、env 约定不变）。
FROM golang:1.24-alpine AS build
RUN apk add --no-cache ca-certificates && \
    CGO_ENABLED=0 go install github.com/minio/minio@latest

FROM alpine:3.20
COPY --from=build /go/bin/minio /usr/bin/minio
EXPOSE 9000 9001
VOLUME ["/data"]
ENTRYPOINT ["minio"]
CMD ["server", "/data"]
