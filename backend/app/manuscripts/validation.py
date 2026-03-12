import io
import zipfile

from fastapi import HTTPException, UploadFile

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {".docx", ".txt", ".pdf"}


def get_extension(filename: str) -> str:
    """Extract and validate file extension."""
    if not filename or "." not in filename:
        raise HTTPException(status_code=422, detail="File must have an extension (.docx, .txt, or .pdf)")
    ext = "." + filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"Unsupported file type: {ext}. Accepted: .docx, .txt, .pdf")
    return ext


async def validate_file(file: UploadFile) -> tuple[bytes, str]:
    """Validate file size, type, and magic bytes. Returns (file_bytes, extension).

    Per DECISION_003 JUDGE amendments:
    - No python-magic dependency. Targeted checks per format.
    - DOCX: validate ZIP with word/document.xml inside.
    - PDF: check %PDF- magic header.
    - TXT: no magic check needed.
    """
    ext = get_extension(file.filename or "")

    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds 10MB limit")

    if len(content) == 0:
        raise HTTPException(status_code=422, detail="File is empty")

    if ext == ".docx":
        _validate_docx(content)
    elif ext == ".pdf":
        _validate_pdf(content)
    elif ext == ".txt":
        _validate_txt(content)

    return content, ext


def _validate_docx(content: bytes) -> None:
    """DOCX is a ZIP containing word/document.xml."""
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise HTTPException(status_code=422, detail="This file does not appear to be a valid .docx file")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            if "word/document.xml" not in zf.namelist():
                raise HTTPException(status_code=422, detail="This file does not appear to be a valid .docx file")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="This file is corrupted and cannot be read")


def _validate_pdf(content: bytes) -> None:
    """PDF must start with %PDF- magic header."""
    if not content[:5].startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail="This file does not appear to be a valid PDF")


def _validate_txt(content: bytes) -> None:
    """TXT must be valid UTF-8. Per JUDGE: no chardet fallback."""
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=422,
            detail="This file does not appear to be UTF-8 encoded. Please re-save as UTF-8.",
        )
