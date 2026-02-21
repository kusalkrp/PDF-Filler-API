# PDF Form Auto-Fill API

A stateless REST API that fills AcroForm PDF fields and returns the result as base64 or a binary file. Built with FastAPI, deployable to Railway, and designed for RapidAPI listing.

---

## Endpoints

### `GET /health`
Uptime check. Required by RapidAPI.

**Response:**
```json
{ "status": "ok", "version": "1.0.0" }
```

---

### `POST /inspect`
Discover all fillable field names and types in a PDF before filling it.

**Query parameters:**
| Parameter | Type | Description |
|---|---|---|
| `raw_names` | boolean | If `true`, each field also includes `raw_name` with the original unnormalized PDF field name. Default is `false`. |

**Form fields (multipart):**
| Field | Type | Description |
|---|---|---|
| `file` | File | PDF file upload *(either this or `pdf_url`)* |
| `pdf_url` | string | URL of a PDF to inspect *(either this or `file`)* |

**Success response:**
```json
{
  "field_count": 2,
  "fields": [
    { "name": "FirstName", "type": "text",     "value": "" },
    { "name": "Agree",     "type": "checkbox", "value": false }
  ]
}
```

**Radio fields:**
- For fields reported as `"type": "radio"`, `options` are the raw PDF appearance-state values (for example `"0"`, `"1"`), not display labels like `"Male"`/`"Female"`.
- Send one of those raw option values in `/fill`.

**Success response (`raw_names=true`)**
```json
{
  "field_count": 1,
  "fields": [
    {
      "name": "Age of Dependent",
      "raw_name": "Age\t of Dependent",
      "type": "text",
      "value": ""
    }
  ]
}
```

---

**Query parameters:**
| Parameter | Type | Description |
|---|---|---|
| `download` | boolean | If `true`, returns the binary PDF directly. Default is `false`. |

**Form fields (multipart):**
| Field | Type | Description |
|---|---|---|
| `file` | File | PDF file upload *(either this or `pdf_url`)* |
| `pdf_url` | string | URL of a PDF to fill *(either this or `file`)* |
| `fields` | string (JSON) | `{"FieldName": "value", "CheckBox": true}` |

**Success response (download=false):**
```json
{
  "filled_pdf_base64": "<base64 string>",
  "fields_filled": 2
}
```

**Success response (download=true):**
Returns a binary `application/pdf` stream with `Content-Disposition: attachment`.

**Decode the result in Python:**
```python
import base64
pdf_bytes = base64.b64decode(response["filled_pdf_base64"])
with open("filled.pdf", "wb") as f:
    f.write(pdf_bytes)
```

---

## Error Codes

| Code | HTTP | Meaning |
|---|---|---|
| `PDF_NOT_FILLABLE` | 422 | PDF has no AcroForm fields |
| `FILE_TOO_LARGE` | 413 | File exceeds `MAX_FILE_SIZE_MB` |
| `INVALID_MIME_TYPE` | 415 | Upload is not `application/pdf` |
| `INVALID_URL_SCHEME` | 400 | URL must be `http` or `https` |
| `DOWNLOAD_FAILED` | 502 | Could not fetch PDF from URL |
| `INVALID_FIELD` | 422 | Field name not found in PDF |
| `INVALID_FIELD_VALUE` | 422 | Field value must be string or boolean |
| `NO_INPUT` | 400 | Neither `file` nor `pdf_url` provided |

All errors return:
```json
{ "error": "ERROR_CODE", "message": "Human-readable description." }
```

---

## Running Locally

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and edit env vars (optional)
cp .env.example .env

# 3. Start the server
uvicorn app.main:app --reload

# 4. Open interactive docs
open http://localhost:8000/docs
```

## Running with Docker

### Using Docker Compose (Recommended)
```bash
docker-compose up --build
```

### Using Docker directly
```bash
docker build -t pdf-filler-api .
docker run -p 8000:8000 --env-file .env.example pdf-filler-api
```

```bash
pytest tests/ -v
```

---

## Deploying to Railway

1. Push this repo to GitHub.
2. Create a new project on [Railway](https://railway.app) → **Deploy from GitHub repo**.
3. Railway detects the `Dockerfile` automatically via `railway.toml`.
4. Set environment variables in Railway's dashboard (see `.env.example`).
5. Railway provides a free `.railway.app` subdomain on first deploy.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `API_VERSION` | `1.0.0` | Version string returned by `/health` |
| `MAX_FILE_SIZE_MB` | `10` | Max PDF upload size in MB |
| `REQUEST_TIMEOUT_SECONDS` | `5` | Timeout for URL downloads |

---

## Project Structure

```
PDF-Filler/
├── app/
│   ├── main.py          # FastAPI app + middleware registration
│   ├── config.py        # Env var config
│   ├── errors.py        # Structured error helpers
│   ├── middleware.py    # Request logging middleware
│   ├── pdf_utils.py     # Core PDF logic
│   └── routers/
│       ├── health.py
│       ├── inspect.py
│       └── fill.py
├── tests/
│   └── test_api.py
├── Dockerfile
├── railway.toml
├── requirements.txt
└── .env.example
```
