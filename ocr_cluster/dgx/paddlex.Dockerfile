# PaddleX serving container: PP-StructureV3 on a source-built PaddlePaddle GPU
# wheel for NVIDIA GB10 (aarch64, compute capability 12.1).
#
# Which stage supplies the compiled wheel to the runtime stage is chosen with
# PADDLE_WHEEL_FROM:
#
#   - paddle-compile (default): compile Paddle from source (hours). Plain
#     `docker compose build` on the GB10 box takes this path; nothing extra to
#     pass.
#   - paddle-wheel-prebuilt: install a wheel copied in via the "wheels" build
#     context. CI takes this path (see ci-ocr-cluster.yml): the wheel is
#     compiled once and cached between runs, so the 4-hour compile only
#     happens when this Dockerfile changes. BuildKit evaluates only the
#     stages in the dependency graph of the requested target, so pointing the
#     runtime stage at paddle-wheel-prebuilt skips the compile entirely --
#     even though paddle-compile is defined above it and would fail without
#     the wheels context if it were evaluated.
ARG PADDLE_WHEEL_FROM=paddle-compile
FROM nvidia/cuda:13.0.0-cudnn-devel-ubuntu24.04 AS paddle-compile

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
#
# The pybind11-stubgen pin below matters: cmake's configure step installs it
# unpinned, and 3.0.0 (2026-09) dropped the run(sub_dir=...) API that
# Paddle v3.4.0's tools/gen_pybind11_stub.py calls -- so the wheel-packing
# step fails after the hours-long compile has already succeeded. 2.5.5 has
# the exact API v3.4.0 targets (verified against both sources).
#
# Submodules are fetched with a retry loop rather than --recurse-submodules:
# the Eigen submodule is hosted on gitlab.com, and gitlab intermittently
# rejects clones under load ("unable to handle this request due to load"),
# which killed one CI run 2 minutes in. `git submodule update` resumes where
# it left off, so retries make forward progress until the fetch completes.
# Submodules are fetched in FULL (no --depth): Paddle's CMake extern-project
# patch steps check out tags (`git checkout v21.12` for protobuf,
# `v0.3.28` for openblas), which fail with "pathspec ... did not match" on
# shallow submodule clones.
WORKDIR /opt
RUN git clone --branch "${PADDLE_GIT_REF}" --depth 1 \
        https://github.com/PaddlePaddle/Paddle.git paddle \
    && cd /opt/paddle \
    && ok=""; for i in 1 2 3 4 5; do \
        if git submodule update --init --recursive; then ok=1; break; fi; \
        echo "submodule fetch failed (attempt ${i}/5); GitLab may be under load; retrying in 60s"; \
        sleep 60; \
    done; [ -n "$ok" ] \
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
    && python -m pip install --no-deps "pybind11-stubgen==2.5.5" \
    && ninja -j"$(nproc)" \
    && mkdir -p /opt/wheels \
    && cp /opt/paddle/build/python/dist/*.whl /opt/wheels/ \
    && python -m pip install /opt/wheels/*.whl \
    && rm -rf /opt/paddle

FROM nvidia/cuda:13.0.0-cudnn-devel-ubuntu24.04 AS paddle-wheel-prebuilt

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

# Runtime dependencies only; the compiler toolchain of paddle-compile is not
# needed here.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        libgl1 libglib2.0-0 libgomp1 wget curl ca-certificates \
        fontconfig fonts-dejavu-core fonts-noto-cjk fonts-wqy-microhei \
        libopenblas-dev liblapack-dev unzip \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

ARG PADDLEOCR_VERSION=">=3.4.0,<3.5"
ARG PADDLEX_VERSION=">=3.4.0,<3.5"

ENV PATH="/opt/venv/bin:${PATH}"

# Install the application packages first, then force-reinstall the locally
# built GPU wheel in case either package declares a CPU Paddle dependency.
RUN python3 -m venv /opt/venv \
    && python -m pip install --upgrade pip \
    && python -m pip install "paddleocr[doc-parser]${PADDLEOCR_VERSION}" "paddlex[serving]${PADDLEX_VERSION}"

# The wheels context carries the wheel produced by paddle-compile (extracted
# and cached between CI runs). This stage is only ever evaluated when the
# build passes PADDLE_WHEEL_FROM=paddle-wheel-prebuilt together with the
# wheels context.
COPY --from=wheels / /tmp/wheels
RUN python -m pip install --force-reinstall /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

FROM ${PADDLE_WHEEL_FROM} AS runtime

ENV PADDLE_PDX_LOCAL_FONT_FILE_PATH=/usr/share/fonts/truetype/wqy/wqy-microhei.ttc

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

# Model warmup runs BEFORE the COPY of shared/entrypoint.sh and paddlex_wrapper.py
# below. The shared script changes often; the models are ~2 GB and never change.
# If the COPY came first, every edit to entrypoint.sh invalidated these layers
# and re-downloaded the whole model set. Keeping the volatile files last makes a
# script edit a seconds-long rebuild, which is what lets `docker compose up
# --build` be the normal way to run this stack.

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

# The PingFang warmup layer was removed: PADDLE_PDX_LOCAL_FONT_FILE_PATH (set
# above) points at the WQY Micro Hei font installed by apt, so the lazy font
# download from Baidu's CDN never fires -- the CDN once rejected the fetch
# mid-build and killed a CI run, and at runtime the font race was the source
# of "OSError: unknown file format" corruption in the visualize path.

USER root
COPY --from=shared entrypoint.sh /usr/local/bin/entrypoint.sh
COPY paddlex_wrapper.py /usr/local/bin/paddlex_wrapper.py
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/paddlex_wrapper.py
USER paddlex

EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
