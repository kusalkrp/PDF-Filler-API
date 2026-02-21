import io
import json
import base64
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from pypdf import PdfReader

from app.main import app
from app import pdf_utils

# ── Fixture: generate minimal AcroForm PDF in memory using pypdf ─────────────

def _make_acroform_pdf(fields: list[str]) -> bytes:
    """Build a minimal AcroForm PDF with text fields using pypdf low-level objects."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject, BooleanObject, DictionaryObject,
        NameObject, NumberObject, TextStringObject
    )
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    # Catalog and AcroForm setup
    if "/AcroForm" not in writer.root_object:
        writer.root_object.update({
            NameObject("/AcroForm"): DictionaryObject({
                NameObject("/Fields"): ArrayObject(),
                NameObject("/NeedAppearances"): BooleanObject(True)
            })
        })

    acro_fields = writer.root_object["/AcroForm"]["/Fields"]

    for name in fields:
        # Create a text field annotation (/Tx = Text)
        field = DictionaryObject({
            NameObject("/FT"): NameObject("/Tx"),
            NameObject("/T"): TextStringObject(name),
            NameObject("/V"): TextStringObject(""),
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Rect"): ArrayObject([
                NumberObject(10), NumberObject(10),
                NumberObject(100), NumberObject(30)
            ]),
        })
        # Add object and link it to the AcroForm and Page
        field_ref = writer._add_object(field)
        acro_fields.append(field_ref)

        if "/Annots" not in page:
            page[NameObject("/Annots")] = ArrayObject()
        page["/Annots"].append(field_ref)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_flat_pdf() -> bytes:
    """Build a plain PDF with no form fields."""
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_radio_pdf() -> bytes:
    """Build a PDF containing a radio group field named Gender with Male/Female options."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    if "/AcroForm" not in writer.root_object:
        writer.root_object.update({
            NameObject("/AcroForm"): DictionaryObject({
                NameObject("/Fields"): ArrayObject(),
                NameObject("/NeedAppearances"): BooleanObject(True),
            })
        })

    acro_fields = writer.root_object["/AcroForm"]["/Fields"]

    radio_parent = DictionaryObject({
        NameObject("/FT"): NameObject("/Btn"),
        NameObject("/Ff"): NumberObject(1 << 15),
        NameObject("/T"): TextStringObject("Gender"),
        NameObject("/V"): NameObject("/Off"),
        NameObject("/Kids"): ArrayObject(),
    })
    radio_parent_ref = writer._add_object(radio_parent)
    acro_fields.append(radio_parent_ref)

    off_stream = DecodedStreamObject()
    off_stream.set_data(b"")
    off_stream.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([
            NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)
        ]),
    })
    off_stream_ref = writer._add_object(off_stream)

    for index, option in enumerate(["Male", "Female"]):
        on_stream = DecodedStreamObject()
        on_stream.set_data(b"")
        on_stream.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject([
                NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)
            ]),
        })
        on_stream_ref = writer._add_object(on_stream)

        option_state = NameObject(f"/{option}")
        normal_appearance = DictionaryObject({
            NameObject("/Off"): off_stream_ref,
            option_state: on_stream_ref,
        })

        kid = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Parent"): radio_parent_ref,
            NameObject("/Rect"): ArrayObject([
                NumberObject(100),
                NumberObject(700 - (index * 30)),
                NumberObject(112),
                NumberObject(712 - (index * 30)),
            ]),
            NameObject("/AP"): DictionaryObject({NameObject("/N"): normal_appearance}),
            NameObject("/AS"): NameObject("/Off"),
        })

        kid_ref = writer._add_object(kid)
        radio_parent["/Kids"].append(kid_ref)

        if "/Annots" not in page:
            page[NameObject("/Annots")] = ArrayObject()
        page["/Annots"].append(kid_ref)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_push_button_pdf() -> bytes:
    """Build a PDF containing a push button field (should be hidden from inspect)."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject, BooleanObject, DictionaryObject,
        NameObject, NumberObject, TextStringObject,
    )

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    writer.root_object.update({
        NameObject("/AcroForm"): DictionaryObject({
            NameObject("/Fields"): ArrayObject(),
            NameObject("/NeedAppearances"): BooleanObject(True),
        })
    })
    acro_fields = writer.root_object["/AcroForm"]["/Fields"]

    push_button = DictionaryObject({
        NameObject("/FT"): NameObject("/Btn"),
        NameObject("/Ff"): NumberObject(1 << 16),
        NameObject("/T"): TextStringObject("ResetButton"),
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/Rect"): ArrayObject([
            NumberObject(100), NumberObject(700), NumberObject(200), NumberObject(720)
        ]),
    })
    ref = writer._add_object(push_button)
    acro_fields.append(ref)
    page[NameObject("/Annots")] = ArrayObject([ref])

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_text_ap_pdf() -> bytes:
    """Build a PDF with a text field that has an authored AP stream with fixed Td coordinates."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    writer.root_object.update({
        NameObject("/AcroForm"): DictionaryObject({
            NameObject("/Fields"): ArrayObject(),
            NameObject("/NeedAppearances"): BooleanObject(True),
            NameObject("/DA"): TextStringObject("/Helv 12 Tf 0 g"),
        })
    })
    acro_fields = writer.root_object["/AcroForm"]["/Fields"]

    ap_stream = DecodedStreamObject()
    ap_stream.set_data(
        (
            "/Tx BMC\n"
            "q\n"
            "BT\n"
            "/Helvetica 12 Tf\n"
            "0 g\n"
            "2 69.828 Td\n"
            "(Test submission) Tj\n"
            "ET\n"
            "Q\n"
            "EMC\n"
        ).encode("latin1")
    )
    ap_stream.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/FormType"): NumberObject(1),
        NameObject("/BBox"): ArrayObject([
            NumberObject(0), NumberObject(0), NumberObject(150), NumberObject(80)
        ]),
    })
    ap_ref = writer._add_object(ap_stream)

    field = DictionaryObject({
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject("ID"),
        NameObject("/V"): TextStringObject("Test submission"),
        NameObject("/MaxLen"): NumberObject(10),
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/Rect"): ArrayObject([
            NumberObject(100), NumberObject(700), NumberObject(250), NumberObject(720)
        ]),
        NameObject("/AP"): DictionaryObject({NameObject("/N"): ap_ref}),
    })
    field_ref = writer._add_object(field)
    acro_fields.append(field_ref)
    page[NameObject("/Annots")] = ArrayObject([field_ref])

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_text_ap_pdf_tj_array() -> bytes:
    """Build a PDF with an authored AP stream using TJ array operator."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    writer.root_object.update({
        NameObject("/AcroForm"): DictionaryObject({
            NameObject("/Fields"): ArrayObject(),
            NameObject("/NeedAppearances"): BooleanObject(True),
            NameObject("/DA"): TextStringObject("/Helv 12 Tf 0 g"),
        })
    })
    acro_fields = writer.root_object["/AcroForm"]["/Fields"]

    ap_stream = DecodedStreamObject()
    ap_stream.set_data(
        (
            "/Tx BMC\n"
            "q\n"
            "BT\n"
            "/Helvetica 12 Tf\n"
            "0 g\n"
            "2 69.828 Td\n"
            "[(OLD) 0] TJ\n"
            "ET\n"
            "Q\n"
            "EMC\n"
        ).encode("latin1")
    )
    ap_stream.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/FormType"): NumberObject(1),
        NameObject("/BBox"): ArrayObject([
            NumberObject(0), NumberObject(0), NumberObject(150), NumberObject(80)
        ]),
    })
    ap_ref = writer._add_object(ap_stream)

    field = DictionaryObject({
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject("ID"),
        NameObject("/V"): TextStringObject("OLD"),
        NameObject("/MaxLen"): NumberObject(10),
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/Rect"): ArrayObject([
            NumberObject(100), NumberObject(700), NumberObject(250), NumberObject(720)
        ]),
        NameObject("/AP"): DictionaryObject({NameObject("/N"): ap_ref}),
    })
    field_ref = writer._add_object(field)
    acro_fields.append(field_ref)
    page[NameObject("/Annots")] = ArrayObject([field_ref])

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ── Shared client fixture ─────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


@pytest.mark.asyncio
async def test_inspect_success(client, tmp_path):
    """Inspect should return the fields of an AcroForm PDF."""
    pdf_bytes = _make_acroform_pdf(["FirstName", "LastName"])
    pdf_path = tmp_path / "acro.pdf"
    pdf_path.write_bytes(pdf_bytes)

    with open(pdf_path, "rb") as f:
        resp = await client.post(
            "/inspect",
            files={"file": ("acro.pdf", f, "application/pdf")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["field_count"] == 2
    field_names = [f["name"] for f in body["fields"]]
    assert "FirstName" in field_names
    assert "LastName" in field_names


@pytest.mark.asyncio
async def test_inspect_raw_names_default_false(client, tmp_path):
    """By default, inspect should not include raw_name keys."""
    pdf_bytes = _make_acroform_pdf(["Age\t of Dependent"])
    pdf_path = tmp_path / "acro_raw.pdf"
    pdf_path.write_bytes(pdf_bytes)

    with open(pdf_path, "rb") as f:
        resp = await client.post(
            "/inspect",
            files={"file": ("acro_raw.pdf", f, "application/pdf")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["fields"][0]["name"] == "Age of Dependent"
    assert "raw_name" not in body["fields"][0]


@pytest.mark.asyncio
async def test_inspect_raw_names_true_includes_original_name(client, tmp_path):
    """inspect?raw_names=true should include both normalized and raw field names."""
    pdf_bytes = _make_acroform_pdf(["Age\t of Dependent"])
    pdf_path = tmp_path / "acro_raw_true.pdf"
    pdf_path.write_bytes(pdf_bytes)

    with open(pdf_path, "rb") as f:
        resp = await client.post(
            "/inspect?raw_names=true",
            files={"file": ("acro_raw_true.pdf", f, "application/pdf")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["fields"][0]["name"] == "Age of Dependent"
    assert body["fields"][0]["raw_name"] == "Age\t of Dependent"


@pytest.mark.asyncio
async def test_inspect_filters_push_buttons(client, tmp_path):
    """Push button fields should be excluded from inspect output."""
    pdf_path = tmp_path / "push_button.pdf"
    pdf_path.write_bytes(_make_push_button_pdf())

    with open(pdf_path, "rb") as f:
        resp = await client.post(
            "/inspect",
            files={"file": ("push_button.pdf", f, "application/pdf")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["field_count"] == 0
    assert body["fields"] == []


@pytest.mark.asyncio
async def test_fill_success(client, tmp_path):
    """Filling an AcroForm PDF should return base64 PDF."""
    pdf_bytes = _make_acroform_pdf(["Name"])
    pdf_path = tmp_path / "acro.pdf"
    pdf_path.write_bytes(pdf_bytes)

    with open(pdf_path, "rb") as f:
        resp = await client.post(
            "/fill",
            files={"file": ("acro.pdf", f, "application/pdf")},
            data={"fields": json.dumps({"Name": "Alice"})},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "filled_pdf_base64" in body
    assert body["fields_filled"] == 1
    # Verify it's valid base64
    decoded = base64.b64decode(body["filled_pdf_base64"])
    assert decoded.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_fill_checkbox_success(client, tmp_path):
    """Filling a checkbox should work without internal error."""
    pdf_bytes = _make_acroform_pdf(["Subscribe"])
    # Note: _make_acroform_pdf in tests creates raw DictionaryObjects,
    # so we're testing the fallback path in our fix.
    pdf_path = tmp_path / "checkbox.pdf"
    pdf_path.write_bytes(pdf_bytes)

    with open(pdf_path, "rb") as f:
        resp = await client.post(
            "/fill",
            files={"file": ("checkbox.pdf", f, "application/pdf")},
            data={"fields": json.dumps({"Subscribe": True})},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_fill_download_success(client, tmp_path):
    """Filling an AcroForm PDF with download=true should return binary PDF."""
    pdf_bytes = _make_acroform_pdf(["Name"])
    pdf_path = tmp_path / "acro.pdf"
    pdf_path.write_bytes(pdf_bytes)

    with open(pdf_path, "rb") as f:
        resp = await client.post(
            "/fill?download=true",
            files={"file": ("acro.pdf", f, "application/pdf")},
            data={"fields": json.dumps({"Name": "Alice"})},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    assert "attachment" in resp.headers["content-disposition"]


@pytest.mark.asyncio
async def test_inspect_flat_pdf_returns_error(client, tmp_path):
    """Inspecting a flat PDF must return PDF_NOT_FILLABLE."""
    pdf_path = tmp_path / "flat.pdf"
    pdf_path.write_bytes(_make_flat_pdf())

    with open(pdf_path, "rb") as f:
        resp = await client.post(
            "/inspect",
            files={"file": ("flat.pdf", f, "application/pdf")},
        )
    assert resp.status_code == 422
    assert resp.json()["error"] == "PDF_NOT_FILLABLE"


@pytest.mark.asyncio
async def test_fill_flat_pdf_returns_error(client, tmp_path):
    """Filling a flat PDF must return PDF_NOT_FILLABLE."""
    pdf_path = tmp_path / "flat.pdf"
    pdf_path.write_bytes(_make_flat_pdf())

    with open(pdf_path, "rb") as f:
        resp = await client.post(
            "/fill",
            files={"file": ("flat.pdf", f, "application/pdf")},
            data={"fields": json.dumps({"Name": "Alice"})},
        )
    assert resp.status_code == 422
    assert resp.json()["error"] == "PDF_NOT_FILLABLE"


@pytest.mark.asyncio
async def test_fill_invalid_json_fields(client, tmp_path):
    """Malformed JSON in fields should return a 400."""
    pdf_path = tmp_path / "flat.pdf"
    pdf_path.write_bytes(_make_flat_pdf())

    with open(pdf_path, "rb") as f:
        resp = await client.post(
            "/fill",
            files={"file": ("flat.pdf", f, "application/pdf")},
            data={"fields": "not-json"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_fill_invalid_dropdown_value_returns_error(client):
    """Dropdown values outside available options should return INVALID_FIELD_VALUE."""
    with open("samples/Sample-Fillable-PDF.pdf", "rb") as f:
        resp = await client.post(
            "/fill",
            files={"file": ("Sample-Fillable-PDF.pdf", f, "application/pdf")},
            data={"fields": json.dumps({"Dropdown2": "Not a valid choice"})},
        )

    assert resp.status_code == 422
    assert resp.json()["error"] == "INVALID_FIELD_VALUE"


@pytest.mark.asyncio
async def test_fill_push_button_rejected_as_invalid_field(client, tmp_path):
    """Push buttons are non-fillable and should be rejected by /fill."""
    pdf_path = tmp_path / "push_button_fill.pdf"
    pdf_path.write_bytes(_make_push_button_pdf())

    with open(pdf_path, "rb") as f:
        resp = await client.post(
            "/fill",
            files={"file": ("push_button_fill.pdf", f, "application/pdf")},
            data={"fields": json.dumps({"ResetButton": True})},
        )

    assert resp.status_code == 422
    assert resp.json()["error"] == "INVALID_FIELD"


@pytest.mark.asyncio
async def test_file_too_large(client, tmp_path, monkeypatch):
    """Files exceeding the size limit should return FILE_TOO_LARGE."""
    import app.config as cfg
    monkeypatch.setattr(cfg, "MAX_FILE_SIZE_BYTES", 10)  # 10 bytes limit

    pdf_path = tmp_path / "big.pdf"
    pdf_path.write_bytes(b"X" * 100)

    with open(pdf_path, "rb") as f:
        resp = await client.post(
            "/inspect",
            files={"file": ("big.pdf", f, "application/pdf")},
        )
    assert resp.status_code == 413
    assert resp.json()["error"] == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_invalid_url_scheme(client):
    """A file:// URL should be rejected as INVALID_URL_SCHEME."""
    resp = await client.post(
        "/inspect",
        data={"pdf_url": "file:///etc/passwd"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_URL_SCHEME"


@pytest.mark.asyncio
async def test_no_input_returns_error(client):
    """Calling /fill with neither file nor pdf_url should return NO_INPUT."""
    resp = await client.post(
        "/fill",
        data={"fields": "{}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "NO_INPUT"


def test_get_fields_treats_non_off_checkbox_as_true():
    """Checkboxes with value /On should be interpreted as checked."""
    source = open("samples/Sample-Fillable-PDF.pdf", "rb").read()
    filled = pdf_utils.fill_pdf(source, {"Option 3": True})
    fields = {f["name"]: f["value"] for f in pdf_utils.get_fields(filled)}
    assert fields["Option 3"] is True


def test_fill_sample_dependent_fields_keep_distinct_values_and_regenerate_ap():
    """Dependent fields should remain distinct and have fresh appearance streams."""
    sample_path = Path("samples/Sample-Fillable-PDF.pdf")
    source = sample_path.read_bytes()

    filled = pdf_utils.fill_pdf(
        source,
        {
            "Name of Dependent": "Jane Doe",
            "Age of Dependent": "10",
        },
    )

    reader = PdfReader(io.BytesIO(filled))
    fields = reader.get_fields()
    assert str(fields["Name of Dependent"].value) == "Jane Doe"
    assert str(fields["Age\t of Dependent"].value) == "10"

    dependent_field = fields["Name of Dependent"].indirect_reference.get_object()
    age_field = fields["Age\t of Dependent"].indirect_reference.get_object()
    assert "/AP" in dependent_field
    assert "/AP" in age_field

    dep_ap_text = (
        dependent_field["/AP"]
        .get_object()["/N"]
        .get_object()
        .get_data()
        .decode("latin1", "ignore")
    )
    age_ap_text = (
        age_field["/AP"]
        .get_object()["/N"]
        .get_object()
        .get_data()
        .decode("latin1", "ignore")
    )

    assert "<004a0061006e006500200044006f0065>" in dep_ap_text  # Jane Doe
    assert "<00310030>" in age_ap_text  # 10


def test_get_fields_identifies_radio_groups():
    """A /Btn field with radio flag should be reported as type=radio with options."""
    fields = pdf_utils.get_fields(_make_radio_pdf())
    gender = next(field for field in fields if field["name"] == "Gender")
    assert gender["type"] == "radio"
    assert gender["value"] == ""
    assert sorted(gender["options"]) == ["Female", "Male"]


def test_fill_radio_group_by_option_name():
    """Filling a radio group should set the selected option by its on-state name."""
    filled = pdf_utils.fill_pdf(_make_radio_pdf(), {"Gender": "Female"})
    fields = {field["name"]: field for field in pdf_utils.get_fields(filled)}
    assert fields["Gender"]["type"] == "radio"
    assert fields["Gender"]["value"] == "Female"


def test_get_fields_includes_dropdown_options():
    """Dropdown fields should include their available options."""
    source = Path("samples/Sample-Fillable-PDF.pdf").read_bytes()
    fields = {field["name"]: field for field in pdf_utils.get_fields(source)}

    dropdown = fields["Dropdown2"]
    assert dropdown["type"] == "dropdown"
    assert dropdown["options"] == ["Choice 1", "Choice 2", "Choice 3", "Choice 4"]


def _make_two_dropdown_pdf() -> bytes:
    """Build a PDF with two independent dropdown fields (City and Language)."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject, BooleanObject, DictionaryObject,
        NameObject, NumberObject, TextStringObject,
    )
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    writer.root_object.update({
        NameObject("/AcroForm"): DictionaryObject({
            NameObject("/Fields"): ArrayObject(),
            NameObject("/NeedAppearances"): BooleanObject(True),
        })
    })
    acro_fields = writer.root_object["/AcroForm"]["/Fields"]

    for name, options, y in [
        ("City",     ["New York", "London", "Berlin"], 700),
        ("Language", ["English", "German", "French"],  650),
    ]:
        field = DictionaryObject({
            NameObject("/FT"):      NameObject("/Ch"),
            NameObject("/T"):       TextStringObject(name),
            NameObject("/V"):       TextStringObject(""),
            NameObject("/Opt"):     ArrayObject([TextStringObject(o) for o in options]),
            NameObject("/Type"):    NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Rect"):    ArrayObject([
                NumberObject(100), NumberObject(y),
                NumberObject(300), NumberObject(y + 20),
            ]),
        })
        ref = writer._add_object(field)
        acro_fields.append(ref)
        if "/Annots" not in page:
            page[NameObject("/Annots")] = ArrayObject()
        page["/Annots"].append(ref)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_fill_dropdown_persists_selected_value():
    """Dropdown fill should persist only the selected option value."""
    source = Path("samples/Sample-Fillable-PDF.pdf").read_bytes()
    filled = pdf_utils.fill_pdf(source, {"Dropdown2": "Choice 3"})

    fields = {field["name"]: field for field in pdf_utils.get_fields(filled)}
    assert fields["Dropdown2"]["value"] == "Choice 3"

    reader = PdfReader(io.BytesIO(filled))
    dropdown_obj = reader.get_fields()["Dropdown2"].indirect_reference.get_object()
    assert str(dropdown_obj.get("/V")) == "Choice 3"
    selected_indexes = dropdown_obj.get("/I")
    assert selected_indexes is not None
    assert int(selected_indexes[0]) == 2


def test_fill_multiple_dropdowns_each_gets_independent_value():
    """Two dropdown fields filled simultaneously should each show their own selected value."""
    source = _make_two_dropdown_pdf()
    filled = pdf_utils.fill_pdf(source, {"City": "Berlin", "Language": "French"})

    fields = {field["name"]: field for field in pdf_utils.get_fields(filled)}
    assert fields["City"]["value"] == "Berlin"
    assert fields["Language"]["value"] == "French"

    reader = PdfReader(io.BytesIO(filled))
    raw_fields = reader.get_fields()
    assert str(raw_fields["City"].indirect_reference.get_object().get("/V")) == "Berlin"
    assert str(raw_fields["Language"].indirect_reference.get_object().get("/V")) == "French"


def test_fill_text_preserves_authored_ap_coordinates():
    """Text fill should patch AP text while preserving authored Td coordinates."""
    source = _make_text_ap_pdf()
    filled = pdf_utils.fill_pdf(source, {"ID": "A123456789"})

    reader = PdfReader(io.BytesIO(filled))
    field_obj = reader.get_fields()["ID"].indirect_reference.get_object()
    ap_content = (
        field_obj["/AP"]
        .get_object()["/N"]
        .get_object()
        .get_data()
        .decode("latin1", "ignore")
    )

    assert "2 69.828 Td" in ap_content
    assert "(A123456789) Tj" in ap_content


def test_fill_text_patches_authored_tj_array_stream():
    """Text AP patching should replace TJ array text while preserving coordinates."""
    source = _make_text_ap_pdf_tj_array()
    filled = pdf_utils.fill_pdf(source, {"ID": "ZXCVBN1234"})

    reader = PdfReader(io.BytesIO(filled))
    field_obj = reader.get_fields()["ID"].indirect_reference.get_object()
    ap_content = (
        field_obj["/AP"]
        .get_object()["/N"]
        .get_object()
        .get_data()
        .decode("latin1", "ignore")
    )

    assert "2 69.828 Td" in ap_content
    assert "[(ZXCVBN1234)] TJ" in ap_content
