# PaddleX serving container: the PaddleOCR-VL pipeline on the host GPU (x86).
#
# Layout detection (PP-DocLayoutV2) and PP-StructureV3 run on the prebuilt
# cu130 paddlepaddle_gpu wheel from Paddle's own package index; the 0.9B VLM
# is not downloaded here, entrypoint.sh points the VLRecognition submodule
# at the vlm-server container instead.
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DEFAULT_TIMEOUT=600 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git libgl1 libglib2.0-0 libgomp1 wget curl ca-certificates \
        fontconfig fonts-dejavu-core fonts-noto-cjk fonts-wqy-microhei \
        libopenblas-dev liblapack-dev unzip \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

ARG PADDLE_GPU_VERSION="3.4.0"
ARG PADDLEOCR_VERSION=">=3.4.0,<3.5"
ARG PADDLEX_VERSION=">=3.4.0,<3.5"

# paddlepaddle_gpu is only published on Paddle's package index, and its CUDA 13
# runtime (the cuda-toolkit metapackage) only on NVIDIA's index. Install the
# application packages first: they declare a CPU paddlepaddle dependency, which
# the --force-reinstall of the GPU wheel below then overwrites.
RUN pip install "paddleocr[doc-parser]${PADDLEOCR_VERSION}" "paddlex[serving]${PADDLEX_VERSION}" \
    && pip install --force-reinstall "paddlepaddle_gpu==${PADDLE_GPU_VERSION}" \
        --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cu130/ \
        --extra-index-url https://pypi.nvidia.com

# The cu130 wheel's .so files link libcuda.so.1 (the driver), so
# `import paddle` fails in a build container that has no GPU driver, even
# when the models below are created with device=cpu. This layer builds a
# stub libcuda.so.1 exporting every cu* symbol paddle references, each
# returning CUDA_ERROR_NO_DEVICE (100): enough to import paddle and
# download models on CPU. At runtime the nvidia container runtime mounts the
# real driver ahead of this stub. The toolchain is installed and removed
# within this one layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev binutils \
    && for f in /usr/local/lib/python3.11/site-packages/paddle/base/*.so /usr/local/lib/python3.11/site-packages/paddle/libs/*.so; do \
           [ -e "$f" ] && nm -D "$f"; \
       done 2>/dev/null | grep ' U ' \
       | awk '$2 ~ /^cu/ && $2 !~ /^(cuda|cublas|cudnn|cusolver|cufft|cusparse|curand|cufile|nccl)/ {s=$2; sub(/@.*/, "", s); print s}' \
       | sort -u \
       | awk '{printf "int %s(void){return 100;}\n", $1}' > /tmp/libcuda_stub.c \
    && echo "stub symbols: $(wc -l < /tmp/libcuda_stub.c)" \
    && test -s /tmp/libcuda_stub.c \
    && gcc -shared -Wl,-soname,libcuda.so.1 -o /usr/local/lib/libcuda.so.1 /tmp/libcuda_stub.c \
    && rm /tmp/libcuda_stub.c \
    && apt-get remove -y --purge gcc libc6-dev binutils \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Pre-fetch the layout model the PaddleOCR-VL pipeline instantiates at serve
# time. device=cpu: the build need not have a real GPU, at runtime the
# service loads the model onto the GPU.
RUN python -c "from paddlex import create_model; create_model('PP-DocLayoutV2', device='cpu')"

# Pre-fetch every PP-StructureV3 submodel into the image.
# left to runtime, the container downloads ~10 models
# on first boot
RUN python -c "from paddlex import create_pipeline; create_pipeline('PP-StructureV3')"

# The PingFang font used to render `visualize` output is lazily downloaded on
# first use, not at model creation, so it needs its own warmup.
RUN python -c "from paddlex.utils.fonts import PINGFANG_FONT; PINGFANG_FONT.path"

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY paddlex_wrapper.py /usr/local/bin/paddlex_wrapper.py
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/paddlex_wrapper.py

EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
