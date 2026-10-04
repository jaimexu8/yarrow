# PaddleX serving container: the PaddleOCR-VL pipeline on CPU (x86).
#
# Layout detection (PP-DocLayoutV2) runs locally on the stock paddlepaddle CPU
# wheel from PyPI; the 0.9B VLM is not downloaded here, entrypoint.sh points
# the VLRecognition submodule at the vlm-server container instead.
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

RUN pip install poetry==1.8.2

WORKDIR /app
COPY paddlex/pyproject.toml paddlex/poetry.lock /app/

RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root

# Pre-fetch the layout model the PaddleOCR-VL pipeline instantiates at serve
# time.
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
