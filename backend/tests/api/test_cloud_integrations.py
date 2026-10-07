class TestCloudStorageIntegration:
    async def test_oauth_authorize_urls(self, client, auth_headers):
        # Google
        res_g = await client.get(
            "/api/v1/integrations/oauth/google/authorize", headers=auth_headers
        )
        assert res_g.status_code == 200
        data_g = res_g.json()
        assert data_g["provider"] == "google"
        assert "accounts.google.com" in data_g["authorization_url"]

        # Dropbox
        res_d = await client.get(
            "/api/v1/integrations/oauth/dropbox/authorize", headers=auth_headers
        )
        assert res_d.status_code == 200
        data_d = res_d.json()
        assert data_d["provider"] == "dropbox"
        assert "dropbox.com" in data_d["authorization_url"]

        # Invalid provider
        res_bad = await client.get(
            "/api/v1/integrations/oauth/invalid_drive/authorize", headers=auth_headers
        )
        assert res_bad.status_code == 400

    async def test_connect_and_sync_workflow(
        self, client, auth_headers, db_session, fake_storage, fake_queue
    ):
        # Initially empty connections
        conn_res = await client.get(
            "/api/v1/integrations/connections", headers=auth_headers
        )
        assert conn_res.status_code == 200
        assert conn_res.json() == []

        # Connect Google account via callback
        connect_res = await client.post(
            "/api/v1/integrations/oauth/google/callback",
            headers=auth_headers,
            json={"provider": "google", "code": "mock_test_code_123"},
        )
        assert connect_res.status_code == 200
        conn_data = connect_res.json()
        assert conn_data["provider"] == "google"
        assert conn_data["account_email"] == "user_google@example.com"

        # Check status endpoint
        status_res = await client.get(
            "/api/v1/integrations/status", headers=auth_headers
        )
        assert status_res.status_code == 200
        statuses = {s["provider"]: s for s in status_res.json()}
        assert statuses["google"]["connected"] is True
        assert statuses["dropbox"]["connected"] is False

        # Trigger sync for Google
        sync_res = await client.post(
            "/api/v1/integrations/google/sync", headers=auth_headers
        )
        assert sync_res.status_code == 200
        sync_data = sync_res.json()
        assert sync_data["imported_count"] >= 1
        assert len(sync_data["files"]) >= 1

        # Check that imported documents show up in library
        docs_res = await client.get("/api/v1/documents/", headers=auth_headers)
        assert docs_res.status_code == 200
        docs = docs_res.json()
        assert len(docs) >= 1
        imported_filename = sync_data["files"][0]
        assert any(d["filename"] == imported_filename for d in docs)

        # Trigger bulk sync
        bulk_sync_res = await client.post(
            "/api/v1/integrations/sync", headers=auth_headers
        )
        assert bulk_sync_res.status_code == 200

        # Disconnect Google
        disc_res = await client.delete(
            "/api/v1/integrations/connections/google", headers=auth_headers
        )
        assert disc_res.status_code == 204

        # Verify disconnected
        status_after = await client.get(
            "/api/v1/integrations/status", headers=auth_headers
        )
        statuses_after = {s["provider"]: s for s in status_after.json()}
        assert statuses_after["google"]["connected"] is False

        # Syncing disconnected provider returns 404
        sync_disc = await client.post(
            "/api/v1/integrations/google/sync", headers=auth_headers
        )
        assert sync_disc.status_code == 404

    async def test_periodic_sync_trigger(self, client, auth_headers):
        res = await client.post(
            "/api/v1/integrations/periodic-sync", headers=auth_headers
        )
        assert res.status_code == 200
        assert "scheduled" in res.json()["message"]
