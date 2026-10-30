# Lightweight orchestrator: crops PP-StructureV3's detected blocks and sends
# each to PaddleOCR-VL for recognition. No PaddleX/GPU dependency here -- it's
# pure HTTP glue, so it stays cheap and CPU-only regardless of cluster size.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi "uvicorn[standard]" requests pillow

COPY hybrid_gateway.py /app/hybrid_gateway.py

EXPOSE 8080
CMD ["python3", "hybrid_gateway.py"]
