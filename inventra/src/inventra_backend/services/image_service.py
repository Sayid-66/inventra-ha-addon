from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import re
import tempfile
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import get_engine, session_scope
from ..revision.change_log import change_set
from .product_service import get_product, update_product, _to_dict

MAX_IMAGE_BYTES = 3 * 1024 * 1024
PRODUCT_ID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


logger = logging.getLogger(__name__)


def image_path(product_id: str, extension: str, digest: str | None = None) -> Path:
    if not re.fullmatch(PRODUCT_ID_PATTERN, product_id) or extension not in MEDIA_TYPES:
        raise HTTPException(422, "Invalid product image path")
    if digest is not None and not re.fullmatch(r"[0-9a-f]{12}", digest):
        raise HTTPException(422, "Invalid product image path")
    directory = (Path(get_settings().db_path).resolve().parent / "product_images").resolve()
    path = (directory / (f"{product_id}.{digest}.{extension}" if digest else f"{product_id}.{extension}")).resolve()
    if not path.is_relative_to(directory):
        raise HTTPException(422, "Invalid product image path")
    return path


def require_product(db: Session, product_id: str):
    product = get_product(db, product_id)
    if product is None or product.deleted_at is not None:
        raise HTTPException(404, "Product not found")
    return product


def image_format(data: bytes, content_type: str) -> str:
    if not data:
        raise HTTPException(422, "Image body must not be empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image exceeds 3 MB limit")
    extension = None
    if data.startswith(b"\xff\xd8\xff"):
        extension = "jpg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = "png"
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        extension = "webp"
    if extension is None or MEDIA_TYPES[extension] != content_type.lower().split(";")[0].strip():
        raise HTTPException(422, "Expected matching JPEG, PNG or WEBP image bytes and Content-Type")
    return extension


def is_uploaded_url(product_id: str, url: str | None) -> bool:
    return bool(url and re.fullmatch(
        re.escape(f"/api/v1/products/{product_id}/image?v=") + r"[0-9a-f]{12}", url))


def _remove_paths(paths) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove obsolete product image")


def remove_images(product_id: str) -> None:
    directory = image_path(product_id, "jpg").parent
    _remove_paths(directory.glob(f"{product_id}.*"))


def remove_uploaded_url(product_id: str, url: str) -> None:
    if is_uploaded_url(product_id, url):
        digest = url.rsplit("=", 1)[1]
        with session_scope(get_engine()) as db:
            product = get_product(db, product_id)
            if product and product.image_url == url and product.deleted_at is None:
                return
            _remove_paths(image_path(product_id, ext, digest) for ext in MEDIA_TYPES)
            _remove_paths(image_path(product_id, ext) for ext in MEDIA_TYPES)


def store_image_transaction(product_id: str, data: bytes, content_type: str) -> dict:
    with session_scope(get_engine()) as db:
        old_paths = list(image_path(product_id, "jpg").parent.glob(f"{product_id}.*"))
        result = store_image(db, product_id, data, content_type)
    # Only remove files observed before this upload, after its commit succeeds.
    with session_scope(get_engine()) as db:
        product = get_product(db, product_id)
        current_url = product.image_url if product else None
        digest = current_url.rsplit("=", 1)[1] if is_uploaded_url(product_id, current_url) else None
        _remove_paths(path for path in old_paths if digest is None or f".{digest}." not in path.name)
    return result


def store_image(db: Session, product_id: str, data: bytes, content_type: str) -> dict:
    product = require_product(db, product_id)
    extension = image_format(data, content_type)
    digest = hashlib.sha256(data).hexdigest()[:12]
    path = image_path(product_id, extension, digest)
    url = f"/api/v1/products/{product_id}/image?v={digest}"
    temporary = None
    try:
        if product.image_url == url and path.is_file() and path.read_bytes() == data:
            return _to_dict(product)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".image-", delete=False) as output:
            temporary = output.name
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise HTTPException(503, "Product image storage is not writable or available") from exc
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                logger.warning("Could not remove temporary product image")
    with change_set(db) as cs:
        result = update_product(db, cs, str(uuid.uuid4()), product_id,
                                version=product.version, image_url=url)
    return result


def find_image(db: Session, product_id: str) -> tuple[Path, str]:
    product = require_product(db, product_id)
    if is_uploaded_url(product_id, product.image_url):
        digest = product.image_url.rsplit("=", 1)[1]
        for extension, media_type in MEDIA_TYPES.items():
            path = image_path(product_id, extension, digest)
            if path.is_file():
                return path, media_type
        # Compatibility with photos uploaded before hash-bearing filenames.
        legacy = [image_path(product_id, ext) for ext in MEDIA_TYPES]
        legacy = sorted((path for path in legacy if path.is_file()),
                        key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for path in legacy:
            if hashlib.sha256(path.read_bytes()).hexdigest()[:12] == digest:
                return path, MEDIA_TYPES[path.suffix[1:]]
    raise HTTPException(404, "Product image not found")
