from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pypdf import PdfReader

from app.source_store import PROJECT_ROOT


INPUT_MATERIALS_DIRECTORY = (
    PROJECT_ROOT / "data" / "runs" / "draft-inputs"
)
MATERIAL_ROLES = {
    "verify",
    "primary_source",
    "context_only",
    "do_not_cite",
}
ADVANCED_SETTING_FIELDS = {
    "period",
    "geography",
    "languages",
    "source_types",
    "report_format",
}


@dataclass(frozen=True)
class PreparedMaterial:
    kind: str
    name: str
    url: str | None
    text_content: str | None
    mime_type: str | None
    content: bytes | None
    content_hash: str
    byte_size: int
    extension: str | None


def default_research_settings(period: str) -> dict:
    return {
        "period": period,
        "geography": "Автоматически",
        "languages": ["Русский", "Английский"],
        "source_types": [
            "Открытые веб-источники",
            "Первичные источники",
        ],
        "report_format": "Редакционный отчёт",
    }


def effective_research_settings(
    auto_settings: dict,
    overrides: dict,
) -> dict:
    return {
        key: overrides.get(key, value)
        for key, value in auto_settings.items()
    }


def validate_material_role(role: str) -> str:
    if role not in MATERIAL_ROLES:
        raise ValueError("Неизвестная роль материала")

    return role


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_filename(filename: str) -> str:
    normalized = filename.strip()

    if (
        not normalized
        or len(normalized) > 255
        or "/" in normalized
        or "\\" in normalized
        or Path(normalized).name != normalized
    ):
        raise ValueError("Некорректное имя файла")

    return normalized


def _normalized_url(value: str) -> str:
    if len(value) > 2_048:
        raise ValueError("Ссылка слишком длинная")

    parsed = urlsplit(value.strip())

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            "Укажите публичную HTTP(S)-ссылку без credentials"
        )

    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )


def _decode_file(
    content_base64: str,
    *,
    max_file_bytes: int,
) -> bytes:
    if len(content_base64) > (
        ((max_file_bytes + 2) // 3) * 4 + 16
    ):
        raise ValueError("Файл превышает допустимый размер")

    try:
        content = base64.b64decode(
            content_base64,
            validate=True,
        )
    except (binascii.Error, ValueError) as error:
        raise ValueError(
            "Файл передан в некорректном base64"
        ) from error

    if not content:
        raise ValueError("Файл пуст")

    if len(content) > max_file_bytes:
        raise ValueError("Файл превышает допустимый размер")

    return content


def _decode_text_file(
    content: bytes,
    *,
    max_text_bytes: int,
) -> str:
    if len(content) > max_text_bytes:
        raise ValueError(
            "Текстовый файл превышает допустимый размер"
        )

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(
            "Текстовый файл должен быть в UTF-8"
        ) from error

    if "\x00" in text:
        raise ValueError(
            "Текстовый файл содержит недопустимые данные"
        )

    return text


def _extract_pdf_text(
    content: bytes,
    *,
    max_text_bytes: int,
) -> str:
    if not content.startswith(b"%PDF-"):
        raise ValueError("Файл не является PDF")

    try:
        reader = PdfReader(io.BytesIO(content))

        if reader.is_encrypted:
            raise ValueError(
                "Зашифрованные PDF не поддерживаются"
            )

        text = "\n\n".join(
            page.extract_text() or ""
            for page in reader.pages
        ).strip()
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(
            "Не удалось прочитать PDF"
        ) from error

    encoded = text.encode("utf-8")

    if len(encoded) > max_text_bytes:
        text = encoded[:max_text_bytes].decode(
            "utf-8",
            errors="ignore",
        )

    return text


def prepare_material(
    *,
    kind: str,
    url: str | None = None,
    text: str | None = None,
    filename: str | None = None,
    mime_type: str | None = None,
    content_base64: str | None = None,
    max_file_bytes: int,
    max_text_bytes: int,
) -> PreparedMaterial:
    if kind == "url":
        if not url:
            raise ValueError("Укажите ссылку")

        normalized_url = _normalized_url(url)
        content = normalized_url.encode("utf-8")
        return PreparedMaterial(
            kind="url",
            name=normalized_url,
            url=normalized_url,
            text_content=None,
            mime_type="text/uri-list",
            content=None,
            content_hash=_content_hash(content),
            byte_size=len(content),
            extension=None,
        )

    if kind == "note":
        normalized_text = (text or "").strip()
        content = normalized_text.encode("utf-8")

        if not normalized_text:
            raise ValueError("Введите текст материала")

        if len(content) > max_text_bytes:
            raise ValueError(
                "Текст превышает допустимый размер"
            )

        return PreparedMaterial(
            kind="note",
            name="Вставленный текст",
            url=None,
            text_content=normalized_text,
            mime_type="text/plain",
            content=None,
            content_hash=_content_hash(content),
            byte_size=len(content),
            extension=None,
        )

    if kind != "file":
        raise ValueError("Неизвестный тип материала")

    safe_name = _safe_filename(filename or "")
    extension = Path(safe_name).suffix.casefold()

    if extension not in {".pdf", ".txt", ".md"}:
        raise ValueError(
            "Поддерживаются только PDF, TXT и Markdown"
        )

    if not content_base64:
        raise ValueError("Передайте содержимое файла")

    content = _decode_file(
        content_base64,
        max_file_bytes=max_file_bytes,
    )
    supplied_mime = (mime_type or "").casefold()

    if extension == ".pdf":
        if supplied_mime not in {
            "",
            "application/pdf",
            "application/octet-stream",
        }:
            raise ValueError(
                "MIME type не соответствует PDF"
            )

        material_kind = "pdf"
        resolved_mime = "application/pdf"
        extracted_text = _extract_pdf_text(
            content,
            max_text_bytes=max_text_bytes,
        )
    else:
        allowed_mimes = {
            "",
            "text/plain",
            "text/markdown",
            "text/x-markdown",
            "application/octet-stream",
        }

        if supplied_mime not in allowed_mimes:
            raise ValueError(
                "MIME type не соответствует текстовому файлу"
            )

        material_kind = (
            "markdown" if extension == ".md" else "text"
        )
        resolved_mime = (
            "text/markdown"
            if material_kind == "markdown"
            else "text/plain"
        )
        extracted_text = _decode_text_file(
            content,
            max_text_bytes=max_text_bytes,
        )

    return PreparedMaterial(
        kind=material_kind,
        name=safe_name,
        url=None,
        text_content=extracted_text,
        mime_type=resolved_mime,
        content=content,
        content_hash=_content_hash(content),
        byte_size=len(content),
        extension=extension,
    )


def store_material_file(
    *,
    tenant_id: uuid.UUID,
    draft_id: uuid.UUID,
    material_id: uuid.UUID,
    extension: str,
    content: bytes,
    root: Path | None = None,
) -> str:
    root = root or INPUT_MATERIALS_DIRECTORY
    directory = root / str(tenant_id) / str(draft_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{material_id}{extension}"

    with path.open("xb") as file:
        file.write(content)

    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def remove_material_file(
    storage_path: str | None,
    *,
    root: Path | None = None,
) -> None:
    if not storage_path:
        return

    root = root or INPUT_MATERIALS_DIRECTORY
    path = Path(storage_path)

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    resolved_path = path.resolve()
    resolved_root = root.resolve()

    if not resolved_path.is_relative_to(resolved_root):
        raise RuntimeError(
            "Material storage path leaves configured root"
        )

    resolved_path.unlink(missing_ok=True)


def render_research_input_context(
    snapshot: dict,
    *,
    max_characters: int = 20_000,
) -> str:
    if not snapshot:
        return ""

    rendered = json.dumps(
        snapshot,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return rendered[:max_characters]
