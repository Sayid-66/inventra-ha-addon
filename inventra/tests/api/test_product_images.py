import hashlib

import pytest

from tests.ids import test_uuid
from inventra_backend.services import image_service

JPEG = b"\xff\xd8\xff" + b"photo bytes"


@pytest.fixture
def photo_product(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    pid = test_uuid("photo-product")
    response = client.post("/api/v1/products", headers=headers, json={
        "id": pid, "operationId": test_uuid("photo-create"), "name": "iPhone", "imageUrl": "https://off/image.jpg"})
    assert response.status_code == 201
    return client, headers, pid


def upload(client, headers, pid, data=JPEG, media="image/jpeg"):
    return client.put(f"/api/v1/products/{pid}/image", content=data,
                      headers={**headers, "Content-Type": media})


def test_photo_sync_get_idempotency_replace_and_delete(photo_product):
    client, headers, pid = photo_product
    before = client.get("/api/v1/sync?since_revision=0", headers=headers).json()["nextRevision"]
    first = upload(client, headers, pid)
    assert first.status_code == 200
    url = first.json()["imageUrl"]
    assert url.endswith(hashlib.sha256(JPEG).hexdigest()[:12])
    assert first.json()["version"] == 2
    fetched = client.get(url, headers=headers)
    assert fetched.content == JPEG
    assert fetched.headers["content-type"] == "image/jpeg"
    assert fetched.headers["cache-control"] == "private, max-age=31536000, immutable"
    delta = client.get(f"/api/v1/sync?since_revision={before}", headers=headers).json()
    assert delta["changes"][0]["snapshot"]["imageUrl"] == url
    assert client.get("/api/v1/snapshot", headers=headers).json()["entities"][0]["snapshot"]["imageUrl"] == url
    assert upload(client, headers, pid).json() == first.json()
    assert client.get(f"/api/v1/sync?since_revision={delta['nextRevision']}", headers=headers).json()["changes"] == []
    png = b"\x89PNG\r\n\x1a\nnew photo"
    second = upload(client, headers, pid, png, "image/png")
    assert second.json()["version"] == 3
    assert second.json()["imageUrl"] != url
    assert not image_service.image_path(pid, "jpg").exists()
    assert client.get(second.json()["imageUrl"], headers=headers).content == png
    deleted = client.request("DELETE", f"/api/v1/products/{pid}", headers=headers,
                             json={"version": 3, "operationId": test_uuid("photo-delete")})
    assert deleted.status_code == 200
    assert not image_service.image_path(pid, "png").exists()
    assert upload(client, headers, pid).status_code == 404
    assert client.get(url, headers=headers).status_code == 404


@pytest.mark.parametrize("data,media,status", [(b"", "image/jpeg", 422),
    (b"fake", "image/jpeg", 422), (JPEG, "image/png", 422),
    (JPEG + b"x" * image_service.MAX_IMAGE_BYTES, "image/jpeg", 413)],
    ids=["empty", "wrong-magic", "wrong-type", "oversize"])
def test_invalid_photo(photo_product, data, media, status):
    client, headers, pid = photo_product
    response = upload(client, headers, pid, data, media)
    assert response.status_code == status
    assert "error" in response.json()
    assert not image_service.image_path(pid, "jpg").exists()


def test_photo_missing_auth_ids_and_missing_file(photo_product):
    client, headers, pid = photo_product
    assert upload(client, {}, pid).status_code == 401
    assert client.get(f"/api/v1/products/{pid}/image").status_code == 401
    assert upload(client, headers, test_uuid("unknown-photo")).status_code == 404
    assert client.get(f"/api/v1/products/{pid}/image", headers=headers).status_code == 404
    assert upload(client, headers, "invalid-id").status_code == 422
    with pytest.raises(Exception) as error:
        image_service.image_path("../escape", "jpg")
    assert error.value.status_code == 422


def test_photo_storage_failure(photo_product, monkeypatch):
    client, headers, pid = photo_product
    def denied(*args, **kwargs):
        raise PermissionError("read only")
    monkeypatch.setattr(image_service.tempfile, "NamedTemporaryFile", denied)
    response = upload(client, headers, pid)
    assert response.status_code == 503
    assert "storage" in response.json()["error"]["message"]


def test_patch_preserves_photo_unless_explicit(photo_product):
    client, headers, pid = photo_product
    url = upload(client, headers, pid).json()["imageUrl"]
    result = client.patch(f"/api/v1/products/{pid}", headers=headers,
        json={"operationId": test_uuid("photo-patch"), "version": 2, "name": "USER NAME"})
    assert result.json()["imageUrl"] == url
    assert result.json()["name"] == "USER NAME"
    result = client.patch(f"/api/v1/products/{pid}", headers=headers,
        json={"operationId": test_uuid("photo-explicit"), "version": 3, "imageUrl": "https://other/photo"})
    assert result.json()["imageUrl"] == "https://other/photo"


def test_reresolve_keeps_uploaded_image(photo_product, monkeypatch):
    import asyncio
    from sqlalchemy.orm import Session
    from inventra_backend.db.base import get_engine
    from inventra_backend.db.models import Barcode
    from inventra_backend.resolver import resolver_service
    from inventra_backend.resolver.source_client import SourceCandidate, SourceResult
    client, headers, pid = photo_product
    url = upload(client, headers, pid).json()["imageUrl"]
    with Session(get_engine()) as db:
        db.add(Barcode(code="4006381333931", product_id=pid, version=1))
        db.commit()
    async def fetch(*args):
        return {"off": SourceResult("off", "FOUND",
            SourceCandidate("Milk", None, None, "https://off/new.jpg", None, None))}
    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fetch)
    result = asyncio.run(resolver_service.re_resolve(pid, "4006381333931"))
    assert result.diff["imageUrl"]["currentValue"] == url
    assert result.diff["imageUrl"]["changed"] is False
    assert result.diff["imageUrl"]["proposedValue"] is None


def test_webp_and_exact_size_limit(photo_product):
    client, headers, pid = photo_product
    webp = b"RIFF" + b"\x04\x00\x00\x00" + b"WEBP"
    data = webp + b"x" * (image_service.MAX_IMAGE_BYTES - len(webp))
    result = upload(client, headers, pid, data, "image/webp")
    assert result.status_code == 200
    fetched = client.get(result.json()["imageUrl"], headers=headers)
    assert fetched.content == data
    assert fetched.headers["content-type"] == "image/webp"


def test_stream_releases_database(photo_product):
    import asyncio
    import httpx
    from sqlalchemy import text
    from inventra_backend.db.base import get_engine, session_scope
    client, headers, pid = photo_product
    async def exercise():
        waiting, release = asyncio.Event(), asyncio.Event()
        async def body():
            yield JPEG[:3]
            waiting.set()
            await release.wait()
            yield JPEG[3:]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://testserver") as ac:
            task = asyncio.create_task(ac.put(f"/api/v1/products/{pid}/image", content=body(), headers={**headers, "Content-Type": "image/jpeg"}))
            await asyncio.wait_for(waiting.wait(), 3)
            def write():
                with session_scope(get_engine()) as db:
                    db.execute(text("UPDATE revision_counter SET current_revision=current_revision WHERE id=0"))
            try:
                await asyncio.wait_for(asyncio.to_thread(write), 2)
            finally:
                release.set()
            assert (await task).status_code == 200
    asyncio.run(exercise())


def test_failed_commit_preserves_old_image(photo_product, monkeypatch):
    from sqlalchemy.orm import Session
    client, headers, pid = photo_product
    old = upload(client, headers, pid).json()["imageUrl"]
    original = Session.commit
    def commit(db):
        if db.info.get("fail_photo"):
            raise RuntimeError("commit failed")
        return original(db)
    original_store = image_service.store_image
    def store(db, *args):
        result = original_store(db, *args)
        db.info["fail_photo"] = True
        return result
    with monkeypatch.context() as scoped:
        scoped.setattr(Session, "commit", commit)
        scoped.setattr(image_service, "store_image", store)
        with pytest.raises(RuntimeError, match="commit failed"):
            upload(client, headers, pid, JPEG + b"replacement")
    fetched = client.get(old, headers=headers)
    assert fetched.content == JPEG
    assert fetched.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("replacement", [None, "https://other/photo"])
def test_patch_photo_cleanup(photo_product, replacement):
    client, headers, pid = photo_product
    url = upload(client, headers, pid).json()["imageUrl"]
    path = image_service.image_path(pid, "jpg", url.rsplit("=", 1)[1])
    response = client.patch(f"/api/v1/products/{pid}", headers=headers, json={"operationId": test_uuid("photo-cleanup"), "version": 2, "imageUrl": replacement})
    assert response.status_code == 200
    assert not path.exists()


@pytest.mark.parametrize("template", ["bestand", "historie", "produktdetail"])
@pytest.mark.parametrize("url,visible", [(None, False), ("/api/v1/products/id/image?v=abc", False), ("http://example/image", True), ("https://example/image", True)])
def test_photo_template(template, url, visible):
    from pathlib import Path
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(Path(image_service.__file__).parents[1] / "templates"), autoescape=True)
    env.globals["ingress_url"] = lambda request, path: path
    product = {"name": "Photo", "productId": "id", "imageUrl": url}
    rendered = env.get_template(template + ".html").render(product=product, products=[product], request=None)
    assert ('<img class="product-image"' in rendered) is visible


def test_content_length_cap(photo_product):
    client, headers, pid = photo_product
    result = client.put(f"/api/v1/products/{pid}/image", content=JPEG, headers={**headers, "Content-Type": "image/jpeg", "Content-Length": str(image_service.MAX_IMAGE_BYTES + 1)})
    assert result.status_code == 413


def test_failed_unlink_logs_and_serves_current_format(photo_product, monkeypatch, caplog):
    from pathlib import Path
    client, headers, pid = photo_product
    old_url = upload(client, headers, pid).json()["imageUrl"]
    old_path = image_service.image_path(pid, "jpg", old_url.rsplit("=", 1)[1])
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == old_path:
            raise PermissionError("simulated cleanup failure")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "unlink", unlink)
        png = b"\x89PNG\r\n\x1a\nreplacement"
        result = upload(client, headers, pid, png, "image/png")
    assert result.status_code == 200
    assert old_path.exists()
    assert "Could not remove obsolete product image" in caplog.text
    fetched = client.get(result.json()["imageUrl"], headers=headers)
    assert fetched.content == png
    assert fetched.headers["content-type"] == "image/png"
