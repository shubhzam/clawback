import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


@dataclass
class OcrResult:
    text: str
    method: str


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _read_image(path: Path) -> str:
    # pytesseract is optional, scanned docs just come back empty without it
    try:
        import pytesseract
        from PIL import Image

        return pytesseract.image_to_string(Image.open(path))
    except Exception as exc:
        logger.warning(f"image ocr unavailable for {path.name}: {exc}")
        return ""


def extract_text(path: Path) -> OcrResult:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            text = _read_pdf(path)
            return OcrResult(text=text, method="pdf_text" if text.strip() else "none")
        if suffix in IMAGE_SUFFIXES:
            text = _read_image(path)
            return OcrResult(text=text, method="tesseract" if text.strip() else "none")
        return OcrResult(text=path.read_text(errors="replace"), method="plain_text")
    except Exception as exc:
        logger.error(f"failed to read {path.name}: {exc}")
        return OcrResult(text="", method="none")
