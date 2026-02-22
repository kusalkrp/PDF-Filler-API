import io
import re
import base64
from urllib.parse import urlparse

import requests
from pypdf import PdfReader, PdfWriter

from app import config
from app import errors


def _normalize_field_name(name: str) -> str:
    """Collapse all whitespace (tabs, multiple spaces) into a single space and strip."""
    return re.sub(r"\s+", " ", name).strip()


def _build_field_name_map(available_fields: dict) -> dict:
    """
    Return a mapping of normalized_name -> original_name for all PDF fields.
    Allows callers to match user-supplied names (which may have tab chars, etc.)
    against the real field names in the PDF.
    """
    return {_normalize_field_name(k): k for k in available_fields}


def _resolve_annot_field_name(annot) -> str | None:
    """Resolve a widget annotation field name from itself or its parent."""
    t = annot.get("/T")
    if t:
        return str(t)

    parent_ref = annot.get("/Parent")
    if parent_ref:
        try:
            parent = parent_ref.get_object()
            pt = parent.get("/T")
            if pt:
                return str(pt)
        except Exception:
            return None
    return None


def _validate_url_scheme(url: str) -> None:
    """Raise an error if the URL scheme is not http or https."""
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise errors.invalid_url_scheme()


def download_pdf(url: str) -> bytes:
    """Fetch a PDF from a URL, enforcing scheme and timeout rules."""
    _validate_url_scheme(url)
    try:
        response = requests.get(
            url,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "PDF-Filler-API/1.0"},
            stream=True,
        )
        response.raise_for_status()
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=8192):
            total += len(chunk)
            if total > config.MAX_FILE_SIZE_BYTES:
                raise errors.file_too_large(config.MAX_FILE_SIZE_MB)
            chunks.append(chunk)
        return b"".join(chunks)
    except requests.exceptions.RequestException:
        raise errors.download_failed(url)


def detect_acroform(pdf_bytes: bytes) -> bool:
    """Return True if the PDF contains an AcroForm with at least one field."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        fields = reader.get_fields()
        return bool(fields)
    except Exception:
        return False


def _field_type(field_obj) -> str:
    """Map a pypdf field type code to a human-readable string."""
    ft = field_obj.field_type
    type_map = {
        "/Tx": "text",
        "/Btn": "checkbox",
        "/Ch": "dropdown",
        "/Sig": "signature",
    }
    return type_map.get(ft, "unknown")


def _field_flags(field_obj) -> int:
    """Return integer /Ff flags for a field object."""
    try:
        raw = field_obj.indirect_reference.get_object()
        return int(raw.get("/Ff", 0) or 0)
    except Exception:
        return 0


def _is_radio_field(field_obj) -> bool:
    """Return True when a /Btn field is a radio button group."""
    radio_flag = 1 << 15
    return bool(_field_flags(field_obj) & radio_flag)


def _is_push_button(field_obj) -> bool:
    """Return True when a /Btn field is a push button (non-fillable action button)."""
    push_button_flag = 1 << 16
    return bool(_field_flags(field_obj) & push_button_flag)


def _extract_on_states_from_widget(widget_obj) -> set[str]:
    """Collect non-Off appearance states from a widget annotation."""
    states: set[str] = set()
    try:
        ap = widget_obj.get("/AP")
        if not ap:
            return states
        ap_obj = ap.get_object()
        normal = ap_obj.get("/N")
        if not normal:
            return states
        normal_obj = normal.get_object()
        if hasattr(normal_obj, "keys"):
            for key in normal_obj.keys():
                key_str = str(key)
                if key_str != "/Off":
                    states.add(key_str)
    except Exception:
        return states
    return states


def _radio_options(field_obj) -> list[str]:
    """Extract radio option values (On states) for a /Btn radio field."""
    options: set[str] = set()
    try:
        raw = field_obj.indirect_reference.get_object()
    except Exception:
        return []

    options.update(_extract_on_states_from_widget(raw))

    kids = raw.get("/Kids")
    if kids:
        for kid_ref in kids:
            try:
                kid = kid_ref.get_object()
            except Exception:
                continue
            options.update(_extract_on_states_from_widget(kid))

    return sorted(state.lstrip("/") for state in options)


def _detect_widget_on_state(widget_obj) -> str:
    """Return this widget's non-Off appearance state (e.g. /Yes, /Male), or /Yes as fallback."""
    on_states = _extract_on_states_from_widget(widget_obj)
    if on_states:
        return sorted(on_states)[0]
    return "/Yes"


def _dropdown_options(field_obj) -> list[str]:
    """Extract dropdown/list options from a /Ch field's /Opt entry."""
    try:
        raw = field_obj.indirect_reference.get_object()
    except Exception:
        return []

    opt_values = raw.get("/Opt")
    if not opt_values:
        return []

    options: list[str] = []
    try:
        for item in opt_values:
            # Some PDFs store options as [export, display]
            if isinstance(item, (list, tuple)) and item:
                options.append(str(item[-1]))
            else:
                options.append(str(item))
    except Exception:
        return []

    return options


def _get_field_da(field_obj) -> str:
    """Get the default appearance string from the field object, with safe fallback."""
    try:
        raw = field_obj.indirect_reference.get_object()
        da = raw.get("/DA")
        if da:
            return str(da)
    except Exception:
        pass
    return "/Helv 10 Tf 0 g"


def _build_dropdown_appearance_stream(annot, selected_value: str, da_string: str, page_resources=None):
    """Build a dropdown widget appearance stream using the field's own DA string."""
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        ArrayObject,
        NumberObject,
        FloatObject,
    )

    # Get field dimensions from /Rect using abs() to handle inverted rects
    width, height = 120.0, 14.0
    try:
        rect = annot.get("/Rect")
        if rect and len(rect) >= 4:
            rect_w = abs(float(rect[2]) - float(rect[0]))
            rect_h = abs(float(rect[3]) - float(rect[1]))
            if rect_w > 0:
                width = rect_w
            if rect_h > 0:
                height = rect_h
    except Exception:
        pass

    # Parse font size from DA string for vertical centering
    font_size = 10.0
    try:
        match = re.search(r"(\d+(?:\.\d+)?)\s+Tf", da_string)
        if match:
            font_size = float(match.group(1))
        if font_size == 0:
            font_size = min(height * 0.65, 12.0)
    except Exception:
        pass

    y_offset = max(1.5, (height - font_size) / 2.0)
    x_offset = 2.0

    escaped = selected_value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    # Use the field DA string for font/color in the text block
    content = (
        "/Tx BMC\n"
        "q\n"
        "BT\n"
        f"{da_string}\n"
        f"{x_offset:.4f} {y_offset:.4f} Td\n"
        f"({escaped}) Tj\n"
        "ET\n"
        "Q\n"
        "EMC\n"
    )

    bbox = ArrayObject([
        NumberObject(0),
        NumberObject(0),
        FloatObject(round(width, 4)),
        FloatObject(round(height, 4)),
    ])

    # Build minimal font resources from DA font key
    font_key = "/Helv"
    try:
        parts = da_string.strip().split()
        if len(parts) >= 2 and parts[0].startswith("/"):
            font_key = parts[0]
    except Exception:
        pass

    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject(font_key): NameObject(font_key)
        })
    })

    # Prefer page resources first — they contain the real fonts used on page.
    if page_resources is not None:
        try:
            resources = page_resources.get_object() if hasattr(page_resources, "get_object") else page_resources
        except Exception:
            pass

    # Prefer existing resources from the current annotation AP when available
    try:
        existing_ap = annot.get("/AP")
        if existing_ap:
            ap_obj = existing_ap.get_object()
            normal = ap_obj.get("/N")
            if normal:
                normal_obj = normal.get_object()
                existing_res = normal_obj.get("/Resources")
                if existing_res:
                    resources = existing_res.get_object() if hasattr(existing_res, "get_object") else existing_res
    except Exception:
        pass

    stream = DecodedStreamObject()
    stream.set_data(content.encode("latin1", "ignore"))
    stream.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/FormType"): NumberObject(1),
        NameObject("/BBox"): bbox,
        NameObject("/Resources"): resources,
    })
    return stream


def _iter_writer_ch_fields(writer):
    """
    Walk the writer's AcroForm field tree and yield (field_name, field_obj, widget_objs)
    for every terminal /Ch (dropdown/listbox) field.
    field_obj  – the object where /V, /I, /DV belong.
    widget_objs – list of objects where /AP belongs (may equal [field_obj] when
                  the field and its widget are the same object).
    """
    try:
        acroform = writer.root_object["/AcroForm"].get_object()
        top_fields = acroform.get("/Fields", [])
    except Exception:
        return

    def _widgets_of(obj):
        """Return the list of widget objects that represent this field visually."""
        kids = obj.get("/Kids")
        if not kids:
            return [obj]  # field is its own widget
        widgets = []
        for kid_ref in kids:
            try:
                kid = kid_ref.get_object()
                subtype = kid.get("/Subtype")
                if subtype and str(subtype) == "/Widget":
                    widgets.append(kid)
            except Exception:
                pass
        return widgets if widgets else [obj]

    def _walk(fields_array, inherited_name: str | None = None):
        for ref in fields_array:
            try:
                obj = ref.get_object()
            except Exception:
                continue
            ft = obj.get("/FT")
            kids = obj.get("/Kids")
            t = obj.get("/T")
            current_name = str(t) if t else inherited_name
            if ft and str(ft) == "/Ch":
                # Terminal dropdown/listbox field
                if current_name:
                    yield current_name, obj, _widgets_of(obj)
            elif kids:
                # Non-terminal node – recurse
                yield from _walk(kids, current_name)

    yield from _walk(top_fields)


def _patch_text_appearance_stream(writer, annot, new_value: str) -> bool:
    """
    Patch an existing text field appearance stream in place.
    Replaces only the first Tj text string, preserving coordinates/metrics.
    Returns True if patched successfully, False if fallback is needed.
    """
    try:
        ap = annot.get("/AP")
        if not ap:
            return False
        ap_obj = ap.get_object()
        normal = ap_obj.get("/N")
        if not normal:
            return False
        normal_obj = normal.get_object()

        existing_content = normal_obj.get_data().decode("latin1", errors="ignore")
        escaped = new_value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

        patched = re.sub(r"\(.*?\)\s*Tj", f"({escaped}) Tj", existing_content, count=1)
        if patched == existing_content:
            patched = re.sub(r"<[0-9A-Fa-f\s]*>\s*Tj", f"({escaped}) Tj", existing_content, count=1)
        if patched == existing_content:
            patched = re.sub(r"\[[^\]]*\]\s*TJ", f"[({escaped})] TJ", existing_content, count=1)
        if patched == existing_content:
            # Some authored AP streams have BT/Td but no Tj/TJ yet. Inject text
            # before the first ET to preserve original coordinates and font.
            patched = re.sub(r"\bET\b", f"({escaped}) Tj\nET", existing_content, count=1)
        if patched == existing_content:
            return False

        normal_obj.set_data(patched.encode("latin1", "ignore"))
        return True
    except Exception:
        return False


def _checkbox_value_to_bool(raw_value) -> bool:
    """Interpret PDF checkbox/radio values where any non-Off state is considered checked."""
    if raw_value is None:
        return False
    value_str = str(raw_value).strip()
    return value_str not in ("", "Off", "/Off", "False", "false")


def get_fields(pdf_bytes: bytes, include_raw_names: bool = False) -> list[dict]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    raw_fields = reader.get_fields() or {}
    result = []
    for name, obj in raw_fields.items():
        normalized_name = _normalize_field_name(name)
        raw_name = str(name)
        ftype = _field_type(obj)
        raw_value = obj.value
        if ftype == "checkbox" and _is_push_button(obj):
            continue
        if ftype == "dropdown":
            value = raw_value if raw_value is not None else ""
            field_data = {
                "name": normalized_name,
                "type": "dropdown",
                "value": value,
                "options": _dropdown_options(obj),
            }
            if include_raw_names:
                field_data["raw_name"] = raw_name
            result.append(field_data)
            continue
        if ftype == "checkbox" and _is_radio_field(obj):
            value_str = "" if raw_value in (None, "", "Off", "/Off") else str(raw_value).lstrip("/")
            field_data = {
                "name": normalized_name,
                "type": "radio",
                "value": value_str,
                "options": _radio_options(obj),
            }
            if include_raw_names:
                field_data["raw_name"] = raw_name
            result.append(field_data)
            continue
        if ftype == "checkbox":
            value = _checkbox_value_to_bool(raw_value)
        else:
            value = raw_value if raw_value is not None else ""
        field_data = {
            "name": normalized_name,
            "type": ftype,
            "value": value,
        }
        if include_raw_names:
            field_data["raw_name"] = raw_name
        result.append(field_data)
    return result


def fill_pdf(pdf_bytes: bytes, fields: dict) -> bytes:
    """
    Fill AcroForm fields and return the updated PDF as bytes.
    - Text fields: filled via update_page_form_field_values (handles fonts)
    - Checkboxes: filled manually via /V and /AS (avoids /AP crash)
    """
    for name, value in fields.items():
        if not isinstance(value, (str, bool)):
            raise errors.invalid_field_value(name)

    reader = PdfReader(io.BytesIO(pdf_bytes))
    available_fields = reader.get_fields() or {}
    available_fields = {
        name: field for name, field in available_fields.items()
        if not (_field_type(field) == "checkbox" and _is_push_button(field))
    }
    name_map = _build_field_name_map(available_fields)

    # Validate and normalize incoming field names
    normalized_fields = {}
    for name, value in fields.items():
        normalized = _normalize_field_name(name)
        real_name = name_map.get(normalized)
        if real_name is None:
            raise errors.invalid_field(name)
        if not isinstance(value, (str, bool)):
            raise errors.invalid_field_value(name)
        normalized_fields[real_name] = value  # use the PDF's real field name

    fields = normalized_fields  # replace for rest of function

    writer = PdfWriter()
    writer.append(reader)

    # Set NeedAppearances so viewers regenerate visual state
    from pypdf.generic import (
        BooleanObject,
        NameObject,
        DictionaryObject,
        ArrayObject,
        NumberObject,
        TextStringObject,
    )

    root = writer.root_object
    if "/AcroForm" not in root:
        root[NameObject("/AcroForm")] = DictionaryObject({
            NameObject("/Fields"): ArrayObject(),
            NameObject("/NeedAppearances"): BooleanObject(True),
        })
    else:
        acroform = root["/AcroForm"].get_object()
        acroform[NameObject("/NeedAppearances")] = BooleanObject(True)

    # Split fields into text, dropdown, checkbox, and radio groups
    text_fields = {}
    dropdown_fields = {}
    checkbox_fields = {}
    radio_fields = {}

    for name, value in fields.items():
        ft = available_fields[name].field_type
        if ft == "/Btn":
            if _is_radio_field(available_fields[name]):
                if not isinstance(value, str):
                    raise errors.invalid_field_value(name)
                options = _radio_options(available_fields[name])
                normalized_value = value.lstrip("/")
                if options and normalized_value not in options:
                    raise errors.invalid_field_value(name)
                radio_fields[name] = normalized_value
            else:
                checkbox_fields[name] = value  # keep as original bool
        elif ft == "/Ch":
            if not isinstance(value, str):
                raise errors.invalid_field_value(name)
            options = _dropdown_options(available_fields[name])
            if options and value not in options:
                raise errors.invalid_field_value(name)
            dropdown_fields[name] = value
        else:
            text_fields[name] = value

    # --- Fill text fields using pypdf helper ---
    if text_fields:
        # Preserve authored AP geometry by patching existing appearance streams
        # in-place when possible, and fall back to pypdf field updates.
        fallback_text_fields = {}
        comb_fallback_text_fields = {}
        for page in writer.pages:
            if "/Annots" not in page:
                continue
            for annot_ref in page["/Annots"]:
                annot = annot_ref.get_object()
                field_name = _resolve_annot_field_name(annot)
                if not field_name or field_name not in text_fields:
                    continue

                value = str(text_fields[field_name])

                # Set logical value first
                annot[NameObject("/V")] = TextStringObject(value)
                annot[NameObject("/DV")] = TextStringObject(value)

                parent_ref = annot.get("/Parent")
                parent_obj = None
                if parent_ref:
                    try:
                        parent_obj = parent_ref.get_object()
                        parent_obj[NameObject("/V")] = TextStringObject(value)
                        parent_obj[NameObject("/DV")] = TextStringObject(value)
                    except Exception:
                        parent_obj = None

                # Patch existing AP in-place where possible
                patched = _patch_text_appearance_stream(writer, annot, value)
                if not patched and parent_obj is not None:
                    patched = _patch_text_appearance_stream(writer, parent_obj, value)

                has_max_len = NameObject("/MaxLen") in annot
                has_comb = bool(int(annot.get("/Ff", 0) or 0) & (1 << 24))
                if parent_obj is not None:
                    has_max_len = has_max_len or (NameObject("/MaxLen") in parent_obj)
                    has_comb = has_comb or bool(int(parent_obj.get("/Ff", 0) or 0) & (1 << 24))

                if patched:
                    continue

                # For fixed-length/comb fields, fall back to forced regeneration
                # so empty grids still get populated if AP patch fails.
                if has_max_len or has_comb:
                    if NameObject("/AP") in annot:
                        del annot[NameObject("/AP")]
                    if parent_obj is not None and NameObject("/AP") in parent_obj:
                        del parent_obj[NameObject("/AP")]
                    comb_fallback_text_fields[field_name] = value
                    continue

                if NameObject("/AP") in annot:
                    del annot[NameObject("/AP")]
                fallback_text_fields[field_name] = value

        if fallback_text_fields:
            for page in writer.pages:
                writer.update_page_form_field_values(
                    page, fallback_text_fields, auto_regenerate=False
                )

        if comb_fallback_text_fields:
            for page in writer.pages:
                writer.update_page_form_field_values(
                    page, comb_fallback_text_fields, auto_regenerate=True
                )

    # --- Fill dropdowns (/Ch) ---
    if dropdown_fields:
        from pypdf.generic import TextStringObject, NameObject, NumberObject

        # Build a map of annotation object id -> page resources
        annot_to_page_resources = {}
        for page in writer.pages:
            page_res = page.get("/Resources")
            if "/Annots" not in page:
                continue
            for annot_ref in page["/Annots"]:
                try:
                    annot_to_page_resources[annot_ref.idnum] = page_res
                except Exception:
                    pass

        for field_name, field_obj, widget_objs in _iter_writer_ch_fields(writer):
            if field_name not in dropdown_fields:
                continue

            selected_value = dropdown_fields[field_name]
            options = _dropdown_options(available_fields[field_name])
            selected_index = options.index(selected_value) if selected_value in options else None
            da_string = _get_field_da(available_fields[field_name])

            # Set value at field level only — let viewer render appearance
            field_obj[NameObject("/V")] = TextStringObject(selected_value)
            field_obj[NameObject("/DV")] = TextStringObject(selected_value)
            if selected_index is not None:
                field_obj[NameObject("/I")] = ArrayObject([NumberObject(selected_index)])

            # Write fresh appearance stream per widget using field DA
            for widget in widget_objs:
                page_res = None
                try:
                    widget_ref = getattr(widget, "indirect_reference", None)
                    if widget_ref:
                        page_res = annot_to_page_resources.get(widget_ref.idnum)
                except Exception:
                    pass

                ap_stream = _build_dropdown_appearance_stream(
                    widget,
                    selected_value,
                    da_string,
                    page_resources=page_res,
                )
                if NameObject("/AP") in widget:
                    del widget[NameObject("/AP")]
                ap_ref = writer._add_object(ap_stream)
                widget[NameObject("/AP")] = DictionaryObject({
                    NameObject("/N"): ap_ref
                })

    # --- Fill checkbox/radio buttons manually ---
    if checkbox_fields or radio_fields:
        selected_radio_state: dict[str, str] = {}
        radio_parent_objs = {}

        for page in writer.pages:
            if "/Annots" not in page:
                continue
            for annot_ref in page["/Annots"]:
                annot = annot_ref.get_object()

                # Resolve field name — check annotation directly, then parent
                field_name = _resolve_annot_field_name(annot)

                if not field_name or field_name not in checkbox_fields:
                    if field_name not in radio_fields:
                        continue

                # Checkbox behavior
                if field_name in checkbox_fields:
                    bool_value = checkbox_fields[field_name]
                    on_state = _detect_widget_on_state(annot)
                    target = NameObject(on_state if bool_value else "/Off")
                    annot[NameObject("/V")] = target
                    annot[NameObject("/AS")] = target
                    continue

                # Radio behavior
                desired = radio_fields[field_name]
                on_state = _detect_widget_on_state(annot)
                is_selected = on_state.lstrip("/") == desired

                annot[NameObject("/AS")] = NameObject(on_state if is_selected else "/Off")
                if is_selected:
                    selected_radio_state[field_name] = on_state

                parent_ref = annot.get("/Parent")
                if parent_ref:
                    try:
                        radio_parent_objs[field_name] = parent_ref.get_object()
                    except Exception:
                        pass
                else:
                    radio_parent_objs[field_name] = annot

        # Set group value (/V) at parent field level for radio groups
        for field_name, desired in radio_fields.items():
            parent_obj = radio_parent_objs.get(field_name)
            if parent_obj is None:
                try:
                    parent_obj = available_fields[field_name].indirect_reference.get_object()
                except Exception:
                    parent_obj = None
            if parent_obj is None:
                continue

            selected_state = selected_radio_state.get(field_name)
            if selected_state:
                parent_obj[NameObject("/V")] = NameObject(selected_state)
            elif desired in ("", "Off"):
                parent_obj[NameObject("/V")] = NameObject("/Off")
            else:
                # Desired value wasn't found among widgets on any page
                raise errors.invalid_field_value(field_name)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def pdf_to_base64(pdf_bytes: bytes) -> str:
    """Encode PDF bytes to a base64 string."""
    return base64.b64encode(pdf_bytes).decode("utf-8")
