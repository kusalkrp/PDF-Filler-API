import json
from typing import Optional

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile

from app import config, errors, pdf_utils

router = APIRouter()


async def _load_pdf(
    request: Request,
    file: Optional[UploadFile],
    pdf_url: Optional[str],
) -> bytes:
    """Resolve PDF bytes from either an uploaded file or a URL."""
    if file is not None:
        if file.content_type and file.content_type != "application/pdf":
            raise errors.invalid_mime_type()

        pdf_bytes = await file.read()

        if len(pdf_bytes) > config.MAX_FILE_SIZE_BYTES:
            raise errors.file_too_large(config.MAX_FILE_SIZE_MB)

        request.state.pdf_source = "upload"
        return pdf_bytes

    elif pdf_url:
        request.state.pdf_source = "url"
        return pdf_utils.download_pdf(pdf_url)

    else:
        raise errors.no_input()


@router.post("/fill", tags=["PDF"])
async def fill_pdf(
    request: Request,
    file: Optional[UploadFile] = File(None, description="PDF file to fill"),
    pdf_url: Optional[str] = Form(None, description="URL of a PDF to fill"),
    fields: str = Form(..., description='JSON object of field name → value, e.g. {"Name":"Alice","Agree":true}'),
    download: bool = Query(False, description="If true, return the binary PDF file directly instead of JSON Base64"),
):
    """
    Fill AcroForm fields in the provided PDF and return the result.

    - Supply the PDF as a **file** upload **or** a **pdf_url** form field.
    - Supply **fields** as a JSON string: `{"FieldName": "value", "CheckboxField": true}`.
    - Set **download=true** to receive the binary PDF directly.
    """
    # Parse fields JSON
    try:
        fields_dict: dict = json.loads(fields)
    except (json.JSONDecodeError, ValueError):
        raise errors._err(400, "INVALID_JSON", "The 'fields' parameter must be a valid JSON object.")

    if not isinstance(fields_dict, dict):
        raise errors._err(400, "INVALID_JSON", "The 'fields' parameter must be a JSON object, not an array or scalar.")

    pdf_bytes = await _load_pdf(request, file, pdf_url)

    if not pdf_utils.detect_acroform(pdf_bytes):
        raise errors.pdf_not_fillable()

    filled_bytes = pdf_utils.fill_pdf(pdf_bytes, fields_dict)

    request.state.fields_count = len(fields_dict)

    if download:
        return Response(
            content=filled_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="filled.pdf"'},
        )

    return {
        "filled_pdf_base64": pdf_utils.pdf_to_base64(filled_bytes),
        "fields_filled": len(fields_dict),
    }
