import uuid

from sqlalchemy import select
from yarrow_db.models import Document, Page, Region, RegionText, User
from yarrow_db.models.region import RegionImage

DOCUMENTS = "/api/v1/documents/"


class TestCaptions:
    async def test_document_tree_has_figure_captions(
        self, client, register, verify, login, db_session
    ):
        reg_payload, reg_response = await register()
        assert reg_response.status_code == 201

        ver_response = await verify(reg_payload["email"])
        assert ver_response.status_code == 200

        token_response = await login(reg_payload["email"], reg_payload["password"])
        assert token_response.status_code == 200

        headers = {"Authorization": f"Bearer {token_response.json()['access_token']}"}
        user = (
            await db_session.execute(
                select(User).where(User.email == reg_payload["email"])
            )
        ).scalar_one()

        document = Document(
            id=uuid.uuid4(),
            owner_id=user.id,
            filename="test_file.pdf",
            file_size_bytes=10,
            file_type="application/pdf",
            storage_key=f"documents/{uuid.uuid4()}/original",
            status="completed",
        )
        page = Page(id=uuid.uuid4(), document_id=document.id, page_number=1)

        captions = ["The engineering fountain", "The hallway"]
        rows = [document, page]
        for idx, caption_text in enumerate(captions):
            figure_region = Region(
                id=uuid.uuid4(),
                page_id=page.id,
                page_number=page.page_number,
                reading_order=2 * idx,
                region_type="figure",
                x0=0,
                y0=0,
                x1=10,
                y1=10,
            )
            caption_region = Region(
                id=uuid.uuid4(),
                page_id=page.id,
                page_number=page.page_number,
                reading_order=2 * idx + 1,
                region_type="paragraph",
                x0=0,
                y0=0,
                x1=10,
                y1=10,
            )
            caption = RegionText(
                id=uuid.uuid4(),
                region_id=caption_region.id,
                text_content=caption_text,
            )
            figure = RegionImage(
                id=uuid.uuid4(),
                region_id=figure_region.id,
                image_key="",
                caption_id=caption.id,
            )
            rows.extend([figure_region, caption_region, figure, caption])

        db_session.add_all(rows)
        await db_session.flush()

        tree_response = await client.get(
            f"{DOCUMENTS}{document.id}/parsed", headers=headers
        )
        assert tree_response.status_code == 200, tree_response.text
        document_tree = tree_response.json()

        assert document_tree["document"]["id"] == str(document.id)
        regions = document_tree["pages"][0]["regions"]
        assert [region["region_type"] for region in regions] == ["figure", "figure"]
        assert [region["caption"] for region in regions] == captions
        assert all(region["text"] is None for region in regions)
