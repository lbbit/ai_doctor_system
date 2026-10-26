# AI 私人医生 — 后端镜像（api 与 worker 同镜像不同 CMD，§1.1/§12.2）

FROM python:3.12-slim AS builder
WORKDIR /build
# build-essential/libpq-dev：psycopg 编译；libmagic1：python-magic 运行时探测
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev libmagic1 \
    && rm -rf /var/lib/apt/lists/*
COPY server/requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 libmagic1 curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /wheels /wheels
COPY server/requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels requirements.txt

# OCR 依赖：rapidocr 会拉入带 GUI 的 opencv-python（容器无 X11，import 即失败）。
# 两者共用同一个 `cv2/` 目录，只装 headless 而不先净身会导致 cv2 目录被删空
# （表现为 cv2.__file__=None、dir(cv2) 为空）。必须先全部卸载，再强制重装 headless。
RUN pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless 2>/dev/null || true; \
    rm -rf /usr/local/lib/python3.12/site-packages/cv2 2>/dev/null || true; \
    pip install --no-cache-dir --force-reinstall --no-deps "opencv-python-headless==4.10.0.84" \
    && python -c "import cv2; assert hasattr(cv2,'cvtColor'); print('cv2 ok', cv2.__version__)" \
    || echo "cv2 unavailable (OCR parser disabled, pipeline continues)"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY server/alembic.ini ./alembic.ini
COPY server/alembic ./alembic
COPY server/app ./app
COPY scripts ./scripts
COPY server/entrypoint.sh ./entrypoint.sh
RUN chmod +x entrypoint.sh

# 启动横幅：确认解析链依赖是否就位（便于线上快速排障）
RUN python - <<'PY' || true
mods = [("pdfplumber", "table"), ("pypdfium2", "page-render"), ("PIL", "image")]
mods.append(("rapidocr_onnxruntime", "ocr"))
for name, role in mods:
    try:
        __import__(name)
        print(f"parser dependency ok: {name} ({role})")
    except Exception as exc:
        print(f"parser dependency missing: {name} ({role}) -> {type(exc).__name__}")
PY

EXPOSE 8000

# 入口：entrypoint 先跑 Alembic 迁移再执行 CMD（api 默认；worker 覆盖 command）
ENTRYPOINT ["./entrypoint.sh"]

# 探活含 DB/Redis/MinIO 依赖（§12.2）
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# 默认 api 入口；worker 用 command 覆盖为：python -m app.worker
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
