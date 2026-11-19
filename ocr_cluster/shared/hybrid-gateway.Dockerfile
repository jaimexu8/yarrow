# Lightweight orchestrator: crops PP-StructureV3's detected blocks and sends
# each to PaddleOCR-VL for recognition. No PaddleX/GPU dependency here -- it's
# pure HTTP glue, so it stays cheap and CPU-only regardless of cluster size.
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

WORKDIR /app

RUN pip install --no-cache-dir fastapi "uvicorn[standard]" requests pillow

COPY hybrid_gateway.py /app/hybrid_gateway.py

EXPOSE 8080
CMD ["python3", "hybrid_gateway.py"]
