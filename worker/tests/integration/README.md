# Worker Tests

## Ground Truth Generation

```bash
docker compose exec \
    -e INFERENCE_SERVICE_URL=http://gateway:8080/layout-parsing \
    -e INFERENCE_API_KEY=... \
    worker python tests/integration/tools/extract_ground_truth.py
```

## Run Tests

```bash
docker exec -w /app/worker yarrow_worker python -m pytest -q
```
