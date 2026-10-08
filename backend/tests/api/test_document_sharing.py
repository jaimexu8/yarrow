import uuid

from yarrow_db.models import Document, Page, Region, RegionText

from tests.conftest import FIXTURES_DIR


def _pdf():
    return (
        "files",
        ("sample.pdf", (FIXTURES_DIR / "sample.pdf").read_bytes(), "application/pdf"),
    )


async def _create_completed_document(
    client, auth_headers, db_session, fake_storage, fake_queue
):
    """Uploads a document and marks it completed with a page and bounding-box region."""
    resp = await client.post(
        "/api/v1/documents/upload", headers=auth_headers, files=[_pdf()]
    )
    assert resp.status_code == 200
    doc_id = uuid.UUID(resp.json()["accepted"][0]["document_id"])

    # Mark document completed and attach page and region
    doc = await db_session.get(Document, doc_id)
    doc.status = "completed"
    doc.page_count = 1

    page = Page(
        id=uuid.uuid4(),
        document_id=doc_id,
        page_number=1,
        status="completed",
        width=612.0,
        height=792.0,
    )
    region = Region(
        id=uuid.uuid4(),
        page_id=page.id,
        region_type="paragraph",
        reading_order=1,
        page_number=1,
        x0=10.0,
        y0=20.0,
        x1=210.0,
        y1=70.0,
        confidence=0.99,
    )
    text = RegionText(
        id=uuid.uuid4(),
        region_id=region.id,
        text_content="Collaborative review text content",
    )
    db_session.add_all([page, region, text])
    await db_session.commit()

    return doc_id, page.id, region.id


class TestDocumentSharing:
    async def test_share_unprocessed_document_is_rejected(
        self, client, auth_headers, register, verify, fake_storage, fake_queue
    ):
        # Upload but keep it queued
        resp = await client.post(
            "/api/v1/documents/upload", headers=auth_headers, files=[_pdf()]
        )
        doc_id = resp.json()["accepted"][0]["document_id"]

        other, _ = await register()
        await verify(other["email"])

        res = await client.post(
            f"/api/v1/documents/{doc_id}/share",
            headers=auth_headers,
            json={"email": other["email"], "permission": "view"},
        )
        assert res.status_code == 409
        detail = res.json()["detail"]
        assert detail.get("code") == "DOCUMENT_NOT_PROCESSED"

    async def test_share_with_unknown_email_is_404(
        self, client, auth_headers, db_session, fake_storage, fake_queue
    ):
        doc_id, _, _ = await _create_completed_document(
            client, auth_headers, db_session, fake_storage, fake_queue
        )
        res = await client.post(
            f"/api/v1/documents/{doc_id}/share",
            headers=auth_headers,
            json={
                "email": "nonexistent_collaborator@example.com",
                "permission": "view",
            },
        )
        assert res.status_code == 404

    async def test_share_with_seeded_local_domain_email_reaches_user_lookup(
        self, client, auth_headers, db_session, fake_storage, fake_queue
    ):
        doc_id, _, _ = await _create_completed_document(
            client, auth_headers, db_session, fake_storage, fake_queue
        )
        # Should not fail with 422 validation error
        res = await client.post(
            f"/api/v1/documents/{doc_id}/share",
            headers=auth_headers,
            json={
                "email": "user@yarrow.local",
                "permission": "view",
            },
        )
        # Either 200 (if user@yarrow.local exists in DB) or 404 (if not in test DB), never 422
        assert res.status_code in (200, 404)

    async def test_share_with_oneself_is_400(
        self, client, auth_headers, db_session, fake_storage, fake_queue
    ):
        me_resp = await client.get("/api/v1/auth/me", headers=auth_headers)
        my_email = me_resp.json()["email"]

        doc_id, _, _ = await _create_completed_document(
            client, auth_headers, db_session, fake_storage, fake_queue
        )
        res = await client.post(
            f"/api/v1/documents/{doc_id}/share",
            headers=auth_headers,
            json={"email": my_email, "permission": "view"},
        )
        assert res.status_code == 400

    async def test_share_and_collaborative_review(
        self,
        client,
        auth_headers,
        db_session,
        register,
        verify,
        login,
        fake_storage,
        fake_queue,
    ):
        doc_id, _page_id, _region_id = await _create_completed_document(
            client, auth_headers, db_session, fake_storage, fake_queue
        )

        # Register collaborator
        collab_payload, _ = await register()
        await verify(collab_payload["email"])
        collab_token = (
            await login(collab_payload["email"], collab_payload["password"])
        ).json()
        collab_headers = {"Authorization": f"Bearer {collab_token['access_token']}"}

        # Collaborator cannot see document before sharing
        before_res = await client.get(
            f"/api/v1/documents/{doc_id}", headers=collab_headers
        )
        assert before_res.status_code == 404

        # Share document with collaborator
        share_res = await client.post(
            f"/api/v1/documents/{doc_id}/share",
            headers=auth_headers,
            json={"email": collab_payload["email"], "permission": "view"},
        )
        assert share_res.status_code == 200
        share_data = share_res.json()
        assert share_data["shared_with_email"] == collab_payload["email"].lower()
        assert share_data["permission"] == "view"

        # List shares as owner
        list_shares_res = await client.get(
            f"/api/v1/documents/{doc_id}/shares", headers=auth_headers
        )
        assert list_shares_res.status_code == 200
        assert len(list_shares_res.json()) == 1

        # Collaborator can now access the document details
        doc_res = await client.get(
            f"/api/v1/documents/{doc_id}", headers=collab_headers
        )
        assert doc_res.status_code == 200
        assert doc_res.json()["id"] == str(doc_id)

        # Collaborator sees it in their document library
        library_res = await client.get("/api/v1/documents/", headers=collab_headers)
        assert library_res.status_code == 200
        assert any(d["id"] == str(doc_id) for d in library_res.json())

        # Collaborator can view parsed bounding-box and page content for collaborative review
        parsed_res = await client.get(
            f"/api/v1/documents/{doc_id}/parsed", headers=collab_headers
        )
        assert parsed_res.status_code == 200
        tree = parsed_res.json()
        assert len(tree["pages"]) == 1
        assert len(tree["pages"][0]["regions"]) == 1
        assert (
            tree["pages"][0]["regions"][0]["text"]
            == "Collaborative review text content"
        )
        assert tree["pages"][0]["regions"][0]["bbox"]["x0"] == 10.0

        # View-only collaborator cannot edit/rename
        rename_res = await client.patch(
            f"/api/v1/documents/{doc_id}",
            headers=collab_headers,
            json={"filename": "renamed_by_collaborator.pdf"},
        )
        assert rename_res.status_code == 403

        # Update permission to review
        update_res = await client.patch(
            f"/api/v1/documents/{doc_id}/shares/{share_data['id']}",
            headers=auth_headers,
            json={"permission": "review"},
        )
        assert update_res.status_code == 200
        assert update_res.json()["permission"] == "review"

        # Reviewer can rename
        rename_ok = await client.patch(
            f"/api/v1/documents/{doc_id}",
            headers=collab_headers,
            json={"filename": "renamed_by_reviewer.pdf"},
        )
        assert rename_ok.status_code == 200

        # Revoke share
        revoke_res = await client.delete(
            f"/api/v1/documents/{doc_id}/shares/{share_data['id']}",
            headers=auth_headers,
        )
        assert revoke_res.status_code == 204

        # Collaborator access revoked
        after_revoke = await client.get(
            f"/api/v1/documents/{doc_id}", headers=collab_headers
        )
        assert after_revoke.status_code == 404
