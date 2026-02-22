import json
from typing import Optional

from fastapi import APIRouter, File, Form, Query, Request, UploadFile

from app import config, errors, pdf_utils

router = APIRouter()


async def _load_pdf(
    request: Request,
    file: Optional[UploadFile],
    pdf_url: Optional[str],
) -> bytes:
    """Resolve PDF bytes from either an uploaded file or a URL."""
    if file is not None:
        # Validate MIME type
        if file.content_type and file.content_type != "application/pdf":
            raise errors.invalid_mime_type()

        pdf_bytes = await file.read()

        # Validate size
        if len(pdf_bytes) > config.MAX_FILE_SIZE_BYTES:
            raise errors.file_too_large(config.MAX_FILE_SIZE_MB)

        request.state.pdf_source = "upload"
        return pdf_bytes

    elif pdf_url:
        request.state.pdf_source = "url"
        return pdf_utils.download_pdf(pdf_url)

    else:
        raise errors.no_input()


@router.post("/inspect", tags=["PDF"])
async def inspect_pdf(
    request: Request,
    file: Optional[UploadFile] = File(None, description="PDF file to inspect"),
    pdf_url: Optional[str] = Form(None, description="URL of a PDF to inspect"),
    raw_names: bool = Query(False, description="If true, include both normalized 'name' and original 'raw_name' for each field."),
):
    """
    Return all fillable AcroForm field names and types found in the PDF.
    Accepts either a file upload or a pdf_url form field.
    """
    pdf_bytes = await _load_pdf(request, file, pdf_url)

    if not pdf_utils.detect_acroform(pdf_bytes):
        raise errors.pdf_not_fillable()

    fields = pdf_utils.get_fields(pdf_bytes, include_raw_names=raw_names)
    request.state.fields_count = len(fields)

    return {"field_count": len(fields), "fields": fields}
