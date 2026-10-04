#!/usr/bin/env bash
# Generates the official PaddleX pipeline config for PP-StructureV3 and hands
# it to `paddlex --serve`. Classic CV pipeline (layout detection -> table
# structure/cell recognition -> OCR -> formula/seal recognition) -- no VLM,
# no external rec-server, so this container is the whole backend.
set -euo pipefail

PIPELINE_NAME="${PIPELINE_NAME:-PP-StructureV3}"
DEVICE="${DEVICE:-cpu}"
PIPELINE_PORT="${PIPELINE_PORT:-8080}"

# Threads each Paddle predictor may use. 
export PADDLEX_CPU_THREADS="${PADDLEX_CPU_THREADS:-1}"

CONFIG_DIR="${HOME}/config"
# `--get_pipeline_config` prompts "Is it covered? (y/N)" if the file exists, which
# is an EOFError with no stdin -- so every restart would crash. Always start clean;
# the config is regenerated deterministically below.
mkdir -p "${CONFIG_DIR}"
rm -f "${CONFIG_DIR}"/*.yaml
paddlex --get_pipeline_config "${PIPELINE_NAME}" --save_path "${CONFIG_DIR}"

CONFIG_PATH="${CONFIG_DIR}/${PIPELINE_NAME}.yaml"
if [ ! -f "${CONFIG_PATH}" ]; then
    CONFIG_PATH="$(find "${CONFIG_DIR}" -maxdepth 1 -name '*.yaml' -print -quit)"
fi

# PP-StructureV3 config rewrite.
#
# Two things happen here, both at *config* level rather than via predict-time
# flags: PaddleX instantiates every submodel listed in the YAML when the
# pipeline is created, so passing `use_formula_recognition=False` to predict()
# still pays for the model's GPU memory. Only removing it from the config does.
#
# 1. Force fine-grained table-cell detection. PaddleX ships the wired/wireless
#    RT-DETR cell-detection models in the config but defaults wireless tables to
#    the end-to-end model, which skips per-cell detection entirely.
#
# 2. STRUCTURE_GEOMETRY_ONLY (default on): drop everything whose *text* output
#    hybrid_gateway.py discards. The gateway keeps PP-StructureV3's boxes and
#    table-cell geometry but replaces every recognizable block's content with
#    PaddleOCR-VL output, so formula/seal/chart recognition and the heavyweight
#    PP-OCRv5_server_{det,rec} models are computed and thrown away. Swapping the
#    server OCR models for their mobile variants and dropping the unused stages
#    measured 4960 MiB -> 2002 MiB of GPU per replica on GB10, with identical
#    layout output (same block count, same labels, same 36 table cells).
#
#    Set STRUCTURE_GEOMETRY_ONLY=0 to keep the full-fidelity pipeline -- required
#    if you ever serve PP-StructureV3 directly instead of behind hybrid_gateway,
#    since its own OCR text quality then matters.
if [ "${PIPELINE_NAME}" = "PP-StructureV3" ] && [ -f "${CONFIG_PATH}" ]; then
    STRUCTURE_GEOMETRY_ONLY="${STRUCTURE_GEOMETRY_ONLY:-1}" python3 - "${CONFIG_PATH}" <<'PYEOF'
import os
import sys
import yaml

path = sys.argv[1]
with open(path) as f:
    config = yaml.safe_load(f)

config["use_e2e_wired_table_rec_model"] = False
config["use_e2e_wireless_table_rec_model"] = False
config["use_wired_table_cells_trans_to_html"] = True
config["use_wireless_table_cells_trans_to_html"] = True
config["use_ocr_results_with_table_cells"] = True

if os.environ["STRUCTURE_GEOMETRY_ONLY"] == "1":
    # Only formula recognition needs disabling: stock PP-StructureV3 already
    # ships use_seal_recognition, use_chart_recognition and use_doc_preprocessor
    # set to False, so restating them changed nothing. If a future PaddleX
    # release flips any of those defaults on, add it back here.
    config["use_formula_recognition"] = False

    # GeneralOCR appears twice (top level and nested under TableRecognition);
    # both instantiate their own copy of the model, so walk the whole tree.
    swaps = {
        "PP-OCRv5_server_det": "PP-OCRv5_mobile_det",
        "PP-OCRv5_server_rec": "PP-OCRv5_mobile_rec",
    }

    def swap(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "model_name" and value in swaps:
                    node[key] = swaps[value]
                else:
                    swap(value)
        elif isinstance(node, list):
            for item in node:
                swap(item)

    swap(config)

with open(path, "w") as f:
    yaml.safe_dump(config, f, sort_keys=False)
PYEOF
fi

# Point the PaddleOCR-VL family's VLRecognition submodule at a remote genai
# server (vLLM/SGLang/FastDeploy) instead of running the VLM in this
# container -- lets a CPU-only layout+client container drive a GPU-backed
# VLM service. Only applies when VLM_SERVER_URL is actually set.
if [[ "${PIPELINE_NAME}" == PaddleOCR-VL* ]] && [ -n "${VLM_SERVER_URL:-}" ] && [ -f "${CONFIG_PATH}" ]; then
    VLM_BACKEND="${VLM_BACKEND:-vllm-server}" VLM_SERVER_URL="${VLM_SERVER_URL}" python3 - "${CONFIG_PATH}" <<'PYEOF'
import os
import sys
import yaml

path = sys.argv[1]
with open(path) as f:
    config = yaml.safe_load(f)

# No model name override needed: GenAIClient auto-discovers the served model
# via the server's /v1/models endpoint when only one model is being served.
config.setdefault("SubModules", {}).setdefault("VLRecognition", {})["genai_config"] = {
    "backend": os.environ["VLM_BACKEND"],
    "server_url": os.environ["VLM_SERVER_URL"],
}

with open(path, "w") as f:
    yaml.safe_dump(config, f, sort_keys=False)
PYEOF
fi

echo "[entrypoint] pipeline   : ${PIPELINE_NAME}"
echo "[entrypoint] device     : ${DEVICE}"
echo "[entrypoint] cpu threads: ${PADDLEX_CPU_THREADS} per worker"
if [ "${PIPELINE_NAME}" = "PP-StructureV3" ]; then
    echo "[entrypoint] geometry-only: ${STRUCTURE_GEOMETRY_ONLY:-1} (1 = formula recognition off, mobile OCR models)"
fi

# paddlex_wrapper.py is the `paddlex` CLI with the cpu_threads default patched -- same
# arguments, same behaviour otherwise.
exec python3 /usr/local/bin/paddlex_wrapper.py --serve --pipeline "${CONFIG_PATH}" \
    --device "${DEVICE}" --host 0.0.0.0 --port "${PIPELINE_PORT}"
