from fastapi import HTTPException


def _err(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": code, "message": message},
    )


def pdf_not_fillable() -> HTTPException:
    return _err(422, "PDF_NOT_FILLABLE", "This PDF has no AcroForm fields. Use /inspect to check before filling.")


def file_too_large(max_mb: int) -> HTTPException:
    return _err(413, "FILE_TOO_LARGE", f"Max file size is {max_mb}MB.")


def invalid_mime_type() -> HTTPException:
    return _err(415, "INVALID_MIME_TYPE", "Only PDF files are accepted (application/pdf).")


def invalid_url_scheme() -> HTTPException:
    return _err(400, "INVALID_URL_SCHEME", "URL must use http or https scheme.")


def download_failed(url: str) -> HTTPException:
    return _err(502, "DOWNLOAD_FAILED", f"Could not fetch PDF from the provided URL: {url}")


def invalid_field(name: str) -> HTTPException:
    return _err(422, "INVALID_FIELD", f"Field '{name}' not found in PDF. Use /inspect to see available fields.")


def invalid_field_value(name: str) -> HTTPException:
    return _err(422, "INVALID_FIELD_VALUE", f"Field '{name}' has an invalid value type. Only strings and booleans are accepted.")


def no_input() -> HTTPException:
    return _err(400, "NO_INPUT", "Provide either a 'file' upload or a 'pdf_url' field.")
