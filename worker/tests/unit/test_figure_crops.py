import base64
import io
from uuid import uuid4

from PIL import Image
from yarrow_db.models import Document, Region
from yarrow_db.models.region import RegionImage

from app.pipeline.document_parser import DocumentParser


def _parser(blocks, width=200, height=100, image_size=(200, 100)):
    """A parser holding one parsed page with the given blocks"""
    buffer = io.BytesIO()
    Image.new("RGB", image_size, "white").save(buffer, format="PNG")

    parser = DocumentParser()
    parser.pages = [base64.b64encode(buffer.getvalue()).decode("utf-8")]
    parser.parsed_result = [
        {
            "result": {
                "layoutParsingResults": [
                    {"prunedResult": {"width": width, "height": height, "parsing_res_list": blocks}}
                ]
            }
        }
    ]
    return parser


def _figure(bbox, label="figure"):
    return {"block_label": label, "block_bbox": bbox, "block_content": ""}


def _build(parser):
    document = Document(id=uuid4())
    objects = parser.to_model_objects(document)
    images = [obj for obj in objects if isinstance(obj, RegionImage)]
    return document, objects, images


def _crop_size(parser, key):
    return Image.open(io.BytesIO(parser.figure_images[key])).size


def test_figure_is_cropped_and_keyed_by_its_region():
    """Test that a figure is cropped and keyed by its region in the storage"""
    parser = _parser([_figure([20, 10, 120, 60])])

    document, objects, images = _build(parser)

    region = next(obj for obj in objects if isinstance(obj, Region))
    assert len(images) == 1
    assert images[0].region_id == region.id
    assert images[0].image_key == f"documents/{document.id}/figures/{region.id}.png"
    assert _crop_size(parser, images[0].image_key) == (100, 50)


def test_every_image_label_is_cropped():
    """Test that every image label is cropped"""
    
    parser = _parser([_figure([0, 0, 10, 10], label) for label in ("image", "figure", "chart", "seal")])

    _, _, images = _build(parser)

    assert len(images) == 4
    assert len(parser.figure_images) == 4


def test_text_regions_are_not_cropped():
    """Test that text regions are not cropped"""
    
    parser = _parser([{"block_label": "text", "block_bbox": [0, 0, 50, 50], "block_content": "Hello"}])

    _, _, images = _build(parser)

    assert images == []
    assert parser.figure_images == {}


def test_bbox_is_scaled_onto_the_page_image():
    """Test that the bounding box is scaled onto the page image"""
    # The OCR reports a page twice the size of the image it was given
    parser = _parser([_figure([40, 20, 240, 120])], width=400, height=200)

    _, _, images = _build(parser)

    assert _crop_size(parser, images[0].image_key) == (100, 50)


def test_bbox_past_the_page_is_clamped():
    """Test that the bounding box is clamped when it extends past the page image"""
    parser = _parser([_figure([150, 50, 300, 300])])

    _, _, images = _build(parser)

    assert _crop_size(parser, images[0].image_key) == (50, 50)


def test_empty_or_missing_bbox_gets_no_image():
    """Test that empty or missing bounding boxes do not produce cropped images"""
    parser = _parser([_figure([50, 50, 50, 80]), _figure([300, 300, 400, 400]), _figure(None)])

    _, objects, images = _build(parser)

    # The figure regions themselves are kept
    assert sum(isinstance(obj, Region) for obj in objects) == 3
    assert images == []


def test_unreadable_page_image_does_not_fail_the_page():
    """Test that an unreadable page image does not cause the page to fail"""
    parser = _parser([_figure([20, 10, 120, 60])])
    parser.pages = [base64.b64encode(b"not an image").decode("utf-8")]

    _, objects, images = _build(parser)

    assert sum(isinstance(obj, Region) for obj in objects) == 1
    assert images == []


def test_crops_from_an_earlier_build_are_dropped():
    """Test that crops from an earlier build are dropped when building again"""
    parser = _parser([_figure([20, 10, 120, 60])])
    _build(parser)
    first_keys = set(parser.figure_images)

    _build(parser)

    assert len(parser.figure_images) == 1
    assert set(parser.figure_images).isdisjoint(first_keys)
