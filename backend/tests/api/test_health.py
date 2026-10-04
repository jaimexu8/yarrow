async def test_health_reports_database_and_queue(client):
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["database"] == "connected"
    assert body["valkey"] == "connected"
