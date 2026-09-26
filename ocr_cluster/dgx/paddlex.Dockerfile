# PaddleX serving container: PP-StructureV3 on a source-built PaddlePaddle GPU
# wheel for NVIDIA GB10 (aarch64, compute capability 12.1).
FROM nvidia/cuda:13.0.0-cudnn-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git cmake ninja-build python3 python3-dev python3-pip python3-venv \
        libgl1 libglib2.0-0 wget curl ca-certificates \
        fontconfig fonts-dejavu-core fonts-noto-cjk fonts-wqy-microhei \
        libopenblas-dev liblapack-dev gfortran patchelf swig unzip \
        libssl-dev zlib1g-dev \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

ARG PADDLE_GIT_REF="v3.4.0"
ARG PADDLE_VERSION="3.4.0"
ARG PADDLEOCR_VERSION=">=3.4.0,<3.5"
ARG PADDLEX_VERSION=">=3.4.0,<3.5"

ENV PATH="/opt/venv/bin:${PATH}" \
    CUDA_HOME=/usr/local/cuda \
    CUDAARCHS=121

RUN python3 -m venv /opt/venv \
    && python -m pip install --upgrade pip \
    && python -m pip install numpy protobuf cython wheel setuptools

# GB10 needs a local ARM64 wheel: published Paddle GPU wheels do not include
# sm_121 and fail at runtime with "Mismatched GPU Architecture".
WORKDIR /opt
RUN git clone --branch "${PADDLE_GIT_REF}" --depth 1 --recurse-submodules \
        https://github.com/PaddlePaddle/Paddle.git paddle \
    && git -C /opt/paddle/third_party/gloo fetch --tags \
    && python -m pip install -r /opt/paddle/python/requirements.txt \
    && mkdir /opt/paddle/build \
    && cd /opt/paddle/build \
    && cmake .. -GNinja \
        -DCMAKE_BUILD_TYPE=Release \
        -DWITH_GPU=ON \
        -DWITH_TESTING=OFF \
        -DCUDA_ARCH_NAME=Manual \
        -DCUDA_ARCH_BIN="12.1" \
        -DWITH_ARM=ON \
        -DWITH_AVX=OFF \
        -DWITH_MKL=OFF \
        -DWITH_MKLDNN=OFF \
        -DWITH_TENSORRT=OFF \
        -DCMAKE_CUDA_FLAGS="-U__ARM_NEON -DEIGEN_DONT_VECTORIZE=1" \
        -DPYTHON_EXECUTABLE=/opt/venv/bin/python \
    && ninja -j"$(nproc)" \
    && mkdir -p /opt/wheels \
    && cp /opt/paddle/build/python/dist/*.whl /opt/wheels/ \
    && python -m pip install /opt/wheels/*.whl \
    && rm -rf /opt/paddle

# Install the application packages first, then restore the locally built GPU
# wheel in case either package declares a CPU Paddle dependency.
RUN python -m pip install "paddleocr[doc-parser]${PADDLEOCR_VERSION}" "paddlex[serving]${PADDLEX_VERSION}" \
    && python -m pip install --force-reinstall /opt/wheels/*.whl

ARG PADDLEX_UID=1001
ARG PADDLEX_GID=1001
RUN groupadd -g "${PADDLEX_GID}" paddlex \
    && useradd -m -s /bin/bash -u "${PADDLEX_UID}" -g "${PADDLEX_GID}" paddlex
ENV HOME=/home/paddlex
WORKDIR /home/paddlex

# Must exist (and be owned by paddlex) at build time: Docker seeds a named
# volume's ownership from the image path it is mounted over. Without this the
# mountpoint is created root-owned and the unprivileged user cannot write.
RUN mkdir -p /home/paddlex/.paddlex /home/paddlex/config \
    && chown -R paddlex:paddlex /home/paddlex

USER paddlex

# Model warmup runs BEFORE the COPY of entrypoint.sh/paddlex_wrapper.py below. Those two
# scripts change often; the models are ~2 GB and never change. With the COPY
# first, every edit to entrypoint.sh invalidated these layers and re-downloaded
# the whole model set. Keeping the volatile files last makes a script edit a
# seconds-long rebuild, which is what lets `docker compose up --build` be the
# normal way to run this stack.

# Pre-fetch every PP-StructureV3 submodel into the image at build time. Left
# to runtime, all N replicas mount the same paddlex-pipeline-cache volume and
# race to download the same ~10 models on first boot -- concurrent writers to
# the same model's temp_dir collide ("temp_dir already exists"), which aborts
# the download and has crashed containers on cold start.
RUN python -c "from paddlex import create_pipeline; create_pipeline('PP-StructureV3')"

# PaddleOCR-VL's layout model, used by the paddleocr-vl-api service. It is not
# part of PP-StructureV3, so without this every one of those replicas downloads
# it independently on first boot -- which once delayed cluster readiness by more
# than ten minutes. device=cpu because that service runs on CPU and because the
# build host need not have a GPU visible.
RUN python -c "from paddlex import create_model; create_model('PP-DocLayoutV2', device='cpu')"

# Same race, different file: the PingFang font used to render `visualize`
# output images is lazily downloaded on first use, not at pipeline creation,
# so it needs its own warmup -- otherwise N replicas write the same font path
# at once and the result is a truncated file ("OSError: unknown file format").
RUN python -c "from paddlex.utils.fonts import PINGFANG_FONT; PINGFANG_FONT.path"

USER root
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY paddlex_wrapper.py /usr/local/bin/paddlex_wrapper.py
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/paddlex_wrapper.py
USER paddlex

EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
