# ort-structure: Paddle-free PP-StructureV3 structure service

The `ort-structure` service replaces `paddlex-pipeline` as the structure backend.

The five models are downloaded at **build time** (pinned to HuggingFace commit
hashes, md5-verified against `MODELS_PINNED.md5`, build fails on mismatch) into
`/opt/ort/models`.

## Endpoints

| Endpoint | Auth | Meaning |
|---|---|---|
| `POST /layout-parsing` | `Authorization: Bearer <STRUCTURE_API_KEY>` (else 401) | Body `{"file": <b64 png>, "fileType": 1}`. Returns the PaddleX `prunedResult` envelope. |
| `GET /health` | none | Shallow liveness. 200 without loading any model. Used by the container runtime / probes. |
| `GET /ready` | none | 200 once the five structure models are loaded, 503 until then. Used by the compose healthcheck. |

`/ready` is the signal that the service can serve work: it returns 503 while the
models are still loading, and 200 once they are. `/health` is deliberately shallower than `/ready` so a probe
never forces a model load.

## Environment

| Var | Default | Meaning |
|---|---|---|
| `MODEL_DIR` | `/opt/ort/models` | Where the five ONNX models live. The build writes them here, so this rarely changes. |
| `STRUCTURE_API_KEY` | (required) | The Bearer key `/layout-parsing` accepts. The hybrid-gateway sends the same key. |
| `CUDA_DEVICE_ID` | `0` | The NVIDIA device the CUDA execution provider binds to. |
| `ORT_DET_MAX_SIDE` | `4000` | Max side (px) of the OCR-detection input. Lower it on a memory-tight host to cap VRAM. |
| `ORT_MEM_CAP` | `0` | Per-model GPU memory caps (0 = off). Enable on a memory-tight host. Note the OCR-det cap only covers a capped input, so keep it off for full det parity. |
| `ORT_ASSERT_GPU` | `1` | Refuse to start if the CUDA execution provider is not actually running inference. This turns a silent CPU fallback into a hard startup failure. |

## Build

```bash
# from the repo root
docker build -f ocr_cluster/shared/ort-structure/Dockerfile \
    -t yarrow-ort-structure:latest ocr_cluster/shared/ort-structure
```

The build pulls `onnxruntime-gpu==1.29.0` plus the four nvidia CUDA 13 runtime
packages (the wheel does not bundle the runtime), the small Python deps, and the
five pinned models, verifying each against its md5. A build takes as long as the
~1 GB model download; the layers are cached on re-run.

## Run

```bash
cd ocr_cluster/x86
cp .env.example .env        # then set GATEWAY_API_KEY, MODEL_NAME, MODEL_VERSION, STRUCTURE_API_KEY
docker compose up -d
```

`docker compose up` starts `ort-structure` (the structure backend) plus the rest
of the x86 stack. The hybrid-gateway posts every page to `STRUCTURE_GATEWAY_URL`,
which defaults to `http://ort-structure:8080/layout-parsing`.

The gateway authenticates to `ort-structure` with the same `STRUCTURE_API_KEY`.
Both are set from `.env` in `docker-compose.yml`.

## Healthcheck / ready semantics

- **healthcheck** uses `GET /ready` with a generous `start_period` (600 s on x86)
  because the five models load on first boot and that is not a failure. During
  `start_period` the 503s from `/ready` do not mark the container unhealthy.
- After `start_period`, `/ready` must return 200 or the healthcheck will restart
  the container.
- `GET /health` is a separate, shallow, always-fast liveness endpoint that never
  loads a model.

## Env var table (compose-level)

| Compose var | Where it lands |
|---|---|
| `STRUCTURE_GATEWAY_URL` | `hybrid-gateway.STRUCTURE_GATEWAY_URL` (the one URL the gateway calls). Default `http://ort-structure:8080/layout-parsing`. |
| `STRUCTURE_API_KEY` | `ort-structure.STRUCTURE_API_KEY` and `hybrid-gateway.STRUCTURE_API_KEY` |
| `CUDA_DEVICE_ID` | `ort-structure.CUDA_DEVICE_ID` |
| `MODEL_DIR` | `ort-structure.MODEL_DIR` (default `/opt/ort/models`) |
| `ORT_MEM_CAP` | `ort-structure.ORT_MEM_CAP` |

## DGX

On the DGX (`ocr_cluster/dgx/docker-compose-inference-node.yml`) the service is
the structure backend by default: `ort-structure` starts with the stack and the
gateway's `STRUCTURE_GATEWAY_URL` defaults to `http://ort-structure:8080/layout-parsing`.
Setting `STRUCTURE_API_KEY` in the node's `.env` is all that is needed (the compose
file requires it, so the stack refuses to come up without it).
