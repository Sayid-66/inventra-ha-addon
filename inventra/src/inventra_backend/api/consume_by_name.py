from __future__ import annotations

import time

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import Device
from ..revision.change_log import change_set
from ..schemas.consume_by_name import ConsumeByNameRequest
from ..services import bring_service
from ..services.consume_by_name_service import consume_by_name

router = APIRouter(prefix="/consumptions", tags=["consumptions-by-name"])


@router.post("/by-name", status_code=201)
def consume_by_name_route(
    body: ConsumeByNameRequest, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        result = consume_by_name(
            db, cs, body.operation_id, device, body.product_name, body.quantity,
            int(time.time() * 1000),
        )
    background_tasks.add_task(bring_service.schedule_stock_change, result["productId"], False)
    return result
