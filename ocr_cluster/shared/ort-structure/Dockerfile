# Paddle-free PP-StructureV3 structure service (the ORT replacement for the
# paddlex-pipeline service). The container runs the verified Paddle-free server
# (ort_structure.server) on onnxruntime-gpu, producing the same prunedResult
# envelope the worker already parses, with no PaddlePaddle runtime.
#
# Five pinned ONNX models (the PP-StructureV3 geometry set) are downloaded from
# HuggingFace at BUILD time, each pinned to a commit hash and md5-verified
# against the manifest in ocr_cluster/shared/ort-structure/. A checksum
# mismatch fails the build. There is no volume mount and no model download at
# runtime: the image is self-contained. The models are ~1 GB, so the image is
# large; the build downloads once and layers are cached on re-run.
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=600

WORKDIR /app

# curl is not in python:3.11-slim (nor wget), and the model download layer
# below uses it. ca-certificates provides the TLS bundle (the base image has
# /etc/ssl/certs/ca-certificates.crt, kept for safety).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The onnxruntime-gpu wheel (pinned below) ships the CUDA EP but NOT the CUDA
# runtime. Its hard link requirements are libcublas.so.13 / libcublasLt.so.13
# (nvidia-cublas), libcudart.so.13 (nvidia-cuda-runtime) and libcurand.so.10
# (nvidia-curand), plus libcuda.so.1 from the host driver (mounted by the
# nvidia container runtime). The CUDA EP also dlopens libcudnn.so.9 for the
# conv/attention kernels (nvidia-cudnn-cu13). The official `[cuda]` extra does
# NOT include nvidia-cublas, so the runtime packages are listed explicitly.
# These land under site-packages/nvidia/{cu13,cudnn}/lib, put on the loader
# path by /etc/ld.so.conf.d/onnxruntime-cuda.conf below.
RUN pip install --no-cache-dir \
        "onnxruntime-gpu==1.29.0" \
        "nvidia-cublas~=13.0" \
        "nvidia-cuda-runtime~=13.0" \
        "nvidia-curand~=10.0" \
        "nvidia-cudnn-cu13~=9.0" \
        numpy opencv-python-headless pillow pyclipper fastapi "uvicorn[standard]"

# Resolve the site-packages path at build time (python:3.11-slim is 3.11) so
# the loader path is version-agnostic. The nvidia wheels install into
# <site>/nvidia/cu13/lib (cublas/cudart/curand) and <site>/nvidia/cudnn/lib.
RUN SITE=$(python3 -c 'import site; print(site.getsitepackages()[0])') \
    && { echo "$SITE/nvidia/cu13/lib"; echo "$SITE/nvidia/cudnn/lib"; } > /etc/ld.so.conf.d/onnxruntime-cuda.conf \
    && ldconfig

# The 5 production models the server loads. MODELS_PINNED.md5 is the single
# source of truth: one line per model, "<md5>  <filename>  <pinned HF URL>" (URL
# pinned to a commit hash, not main), derived from ort-eval/results/parity/
# MODELS_PINNED.md (and .md5). download_models.py downloads each URL with curl
# (curl is not in python:3.11-slim, hence the apt install above) into
# /opt/ort/models and md5-verifies each against the manifest; any download
# failure or checksum mismatch exits nonzero, which fails the build.
COPY MODELS_PINNED.md5 download_models.py /opt/ort/

RUN python3 /opt/ort/download_models.py

# The verified package (ort_structure/ with its server module). Only the
# ort_structure/ subdirectory is copied from the build context, so the
# sibling tests/ directory (CPU-only, runs in CI) is never baked into the
# serving image.
COPY ort_structure/ /app/ort_structure/

# The CUDA EP dlopens the nvidia runtime libs at import time; without them on
# the loader path ORT silently falls back to CPU. The server's ORT_ASSERT_GPU
# (default on) turns that silent fallback into a startup failure.
EXPOSE 8080

CMD ["uvicorn", "ort_structure.server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
