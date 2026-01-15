import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, FastAPI, HTTPException, status

from app.database import get_db
from app.intent import (
    ShiftRequestMessageIntent,
    parse_shift_request_message_intent,
)
from app.models import InboundMessage, ShiftFanout, ShiftFanoutStatus
from app.notifier import place_phone_call, send_sms

router = APIRouter()

_claim_locks: dict[str, asyncio.Lock] = {}
_locks_lock = asyncio.Lock()

# Configurable escalation delay (in seconds) - allows shorter delays for testing
ESCALATION_DELAY_SECONDS = 600  # 10 minutes


async def _get_claim_lock(shift_id: str) -> asyncio.Lock:
    """Get or create a lock for a specific shift to prevent race conditions."""
    async with _locks_lock:
        if shift_id not in _claim_locks:
            _claim_locks[shift_id] = asyncio.Lock()
        return _claim_locks[shift_id]


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/shifts/{shift_id}/fanout")
async def fanout_shift(shift_id: str) -> dict[str, str]:
    """
    Trigger fanout for a shift. Sends SMS to matching caregivers.
    Idempotent: re-posting won't send duplicate notifications.
    """
    db = get_db()

    shift = db.shifts.get(shift_id)
    if shift is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shift {shift_id} not found",
        )

    existing_fanout = db.fanouts.get(shift_id)
    if existing_fanout is not None:
        return {
            "status": "already_fanout",
            "message": f"Fanout already initiated for shift {shift_id}",
        }

    matching_caregivers = db.get_caregivers_by_role(shift.role_required)
    if not matching_caregivers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No caregivers found with role {shift.role_required}",
        )

    now = datetime.now(UTC)
    contacted_ids = [c.id for c in matching_caregivers]

    shift_message = (
        f"New shift available: {shift.start_time.strftime('%Y-%m-%d %H:%M')} "
        f"to {shift.end_time.strftime('%Y-%m-%d %H:%M')}. "
        f"Reply 'yes' or 'accept' to claim."
    )

    for caregiver in matching_caregivers:
        await send_sms(caregiver.phone, shift_message)

    fanout = ShiftFanout(
        shift_id=shift_id,
        status=ShiftFanoutStatus.PENDING,
        created_at=now,
        sms_sent_at=now,
        contacted_caregiver_ids=contacted_ids,
    )
    db.fanouts.put(shift_id, fanout)

    _ = asyncio.create_task(_schedule_escalation(shift_id, now))

    return {
        "status": "fanout_initiated",
        "message": f"Sent SMS to {len(matching_caregivers)} caregivers",
    }


async def _schedule_escalation(shift_id: str, created_at: datetime) -> None:
    """Schedule phone call escalation after configured delay if shift not claimed."""
    await asyncio.sleep(ESCALATION_DELAY_SECONDS)

    db = get_db()
    fanout = db.fanouts.get(shift_id)
    if fanout is None or fanout.status != ShiftFanoutStatus.PENDING:
        return

    shift = db.shifts.get(shift_id)
    if shift is None:
        return

    matching_caregivers = db.get_caregivers_by_role(shift.role_required)
    now = datetime.now(UTC)

    shift_message = (
        f"Shift still available: {shift.start_time.strftime('%Y-%m-%d %H:%M')} "
        f"to {shift.end_time.strftime('%Y-%m-%d %H:%M')}. "
        f"Reply 'yes' or 'accept' to claim."
    )

    for caregiver in matching_caregivers:
        await place_phone_call(caregiver.phone, shift_message)

    fanout.status = ShiftFanoutStatus.ESCALATED
    fanout.phone_call_sent_at = now
    db.fanouts.put(shift_id, fanout)


@router.post("/messages/inbound")
async def handle_inbound_message(message: InboundMessage) -> dict[str, str]:
    """
    Handle incoming SMS or phone messages from caregivers.
    Processes accept/decline intents and claims shifts.
    """
    db = get_db()

    if message.timestamp is None:
        message.timestamp = datetime.now(UTC)

    caregiver = db.get_caregiver_by_phone(message.from_phone)
    if caregiver is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Caregiver with phone {message.from_phone} not found",
        )

    intent = await parse_shift_request_message_intent(message.message)

    if intent != ShiftRequestMessageIntent.ACCEPT:
        return {
            "status": "processed",
            "message": "Message received but not an acceptance",
        }

    fanout = None
    claimed_fanout = None
    for f in db.fanouts.all():
        if caregiver.id in f.contacted_caregiver_ids:
            if f.status == ShiftFanoutStatus.PENDING:
                fanout = f
                break
            elif f.status == ShiftFanoutStatus.CLAIMED:
                claimed_fanout = f

    if fanout is None:
        if claimed_fanout is not None:
            return {
                "status": "shift_already_claimed",
                "message": "This shift has already been claimed",
            }
        return {
            "status": "no_pending_shift",
            "message": "No pending shift found for this caregiver",
        }

    lock = await _get_claim_lock(fanout.shift_id)
    async with lock:
        fanout = db.fanouts.get(fanout.shift_id)
        if fanout is None or fanout.status != ShiftFanoutStatus.PENDING:
            return {
                "status": "shift_already_claimed",
                "message": "This shift has already been claimed",
            }

        fanout.status = ShiftFanoutStatus.CLAIMED
        fanout.claimed_by = caregiver.id
        db.fanouts.put(fanout.shift_id, fanout)

    return {
        "status": "shift_claimed",
        "message": f"Shift {fanout.shift_id} claimed by {caregiver.name}",
    }


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app
