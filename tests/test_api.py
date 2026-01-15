import asyncio
from datetime import UTC

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api import clear_escalation_tasks, create_app
from app.database import get_db, load_sample_data
from app.models import ShiftFanoutStatus


@pytest_asyncio.fixture
async def client():
    """
    Test fixture that creates an async client for the API.
    """
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        yield async_client


@pytest_asyncio.fixture(autouse=True)
def reset_db():
    """Reset database and clear task references before each test."""
    import app.database

    # Clear task references from previous test
    clear_escalation_tasks()

    app.database._db = None
    db = get_db()
    db.shifts.clear()
    db.caregivers.clear()
    db.fanouts.clear()
    load_sample_data()
    yield


@pytest.mark.asyncio
async def test_fanout_shift_not_found(client: AsyncClient) -> None:
    """Test fanout endpoint returns 404 for non-existent shift."""
    response = await client.post("/shifts/nonexistent/fanout")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_fanout_shift_success(client: AsyncClient) -> None:
    """Test successful fanout of a shift."""
    shift_id = "f5a9d844-ecff-4f7a-8ef7-d091f22ad77e"

    response = await client.post(f"/shifts/{shift_id}/fanout")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "fanout_initiated"
    assert "Sent SMS to" in data["message"]

    db_instance = get_db()
    fanout = db_instance.fanouts.get(shift_id)
    assert fanout is not None
    assert fanout.status.value == "pending"
    assert len(fanout.contacted_caregiver_ids) == 1


@pytest.mark.asyncio
async def test_fanout_idempotent(client: AsyncClient) -> None:
    """Test that fanout is idempotent - calling twice doesn't send duplicates."""
    shift_id = "f5a9d844-ecff-4f7a-8ef7-d091f22ad77e"

    response1 = await client.post(f"/shifts/{shift_id}/fanout")
    assert response1.status_code == 200

    response2 = await client.post(f"/shifts/{shift_id}/fanout")
    assert response2.status_code == 200
    data = response2.json()
    assert data["status"] == "already_fanout"


@pytest.mark.asyncio
async def test_inbound_message_accept(client: AsyncClient) -> None:
    """Test accepting a shift via inbound message."""
    shift_id = "f5a9d844-ecff-4f7a-8ef7-d091f22ad77e"

    await client.post(f"/shifts/{shift_id}/fanout")

    message = {
        "from_phone": "+15550001",
        "message": "yes",
    }
    response = await client.post("/messages/inbound", json=message)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "shift_claimed"

    db_instance = get_db()
    fanout = db_instance.fanouts.get(shift_id)
    assert fanout is not None
    assert fanout.status.value == "claimed"
    assert fanout.claimed_by == "27e8d156-7fee-4f79-94d7-b45d306724d4"


@pytest.mark.asyncio
async def test_inbound_message_decline(client: AsyncClient) -> None:
    """Test declining a shift via inbound message."""
    shift_id = "f5a9d844-ecff-4f7a-8ef7-d091f22ad77e"

    await client.post(f"/shifts/{shift_id}/fanout")

    message = {
        "from_phone": "+15550001",
        "message": "no",
    }
    response = await client.post("/messages/inbound", json=message)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"

    db_instance = get_db()
    fanout = db_instance.fanouts.get(shift_id)
    assert fanout.status.value == "pending"


@pytest.mark.asyncio
async def test_inbound_message_caregiver_not_found(client: AsyncClient) -> None:
    """Test inbound message from unknown caregiver."""
    message = {
        "from_phone": "+15559999",
        "message": "yes",
    }
    response = await client.post("/messages/inbound", json=message)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_race_condition_prevention(client: AsyncClient) -> None:
    """Test that only one caregiver can claim a shift."""
    shift_id = "f5a9d844-ecff-4f7a-8ef7-d091f22ad77e"

    await client.post(f"/shifts/{shift_id}/fanout")

    message1 = {"from_phone": "+15550001", "message": "yes"}
    message2 = {"from_phone": "+15550001", "message": "yes"}

    response1 = await client.post("/messages/inbound", json=message1)
    response2 = await client.post("/messages/inbound", json=message2)

    assert response1.status_code == 200
    assert response1.json()["status"] == "shift_claimed"

    assert response2.status_code == 200
    assert response2.json()["status"] == "shift_already_claimed"

    db_instance = get_db()
    fanout = db_instance.fanouts.get(shift_id)
    assert fanout.claimed_by == "27e8d156-7fee-4f79-94d7-b45d306724d4"


@pytest.mark.asyncio
async def test_fanout_no_matching_caregivers(client: AsyncClient) -> None:
    """Test fanout returns 404 when no caregivers match the required role."""
    from datetime import datetime

    from app.models import Shift

    db = get_db()
    shift_id = "test-shift-no-caregivers"

    shift = Shift(
        id=shift_id,
        organization_id="test-org",
        role_required="CNA",
        start_time=datetime(2025, 7, 2, 8, 0, 0, tzinfo=UTC),
        end_time=datetime(2025, 7, 2, 16, 0, 0, tzinfo=UTC),
    )
    db.shifts.put(shift_id, shift)

    response = await client.post(f"/shifts/{shift_id}/fanout")
    assert response.status_code == 404
    assert "No caregivers found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_fanout_multiple_caregivers_same_role(
    client: AsyncClient,
) -> None:
    """Test fanout contacts all caregivers with matching role."""
    shift_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    response = await client.post(f"/shifts/{shift_id}/fanout")
    assert response.status_code == 200

    db_instance = get_db()
    fanout = db_instance.fanouts.get(shift_id)
    assert fanout is not None
    assert len(fanout.contacted_caregiver_ids) == 2


@pytest.mark.asyncio
async def test_inbound_message_unknown_intent(client: AsyncClient) -> None:
    """Test handling of UNKNOWN intent messages."""
    shift_id = "f5a9d844-ecff-4f7a-8ef7-d091f22ad77e"

    await client.post(f"/shifts/{shift_id}/fanout")

    message = {
        "from_phone": "+15550001",
        "message": "maybe",
    }
    response = await client.post("/messages/inbound", json=message)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"

    db_instance = get_db()
    fanout = db_instance.fanouts.get(shift_id)
    assert fanout.status.value == "pending"


@pytest.mark.asyncio
async def test_inbound_message_no_pending_shift(client: AsyncClient) -> None:
    """Test message from caregiver with no pending shifts."""
    message = {
        "from_phone": "+15550001",
        "message": "yes",
    }
    response = await client.post("/messages/inbound", json=message)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "no_pending_shift"


@pytest.mark.asyncio
async def test_escalation_phone_calls_sent(client: AsyncClient) -> None:
    """Test that phone calls are sent after escalation delay if shift not claimed."""
    import app.api

    shift_id = "f5a9d844-ecff-4f7a-8ef7-d091f22ad77e"

    # Use a very short escalation delay for testing
    original_delay = app.api.ESCALATION_DELAY_SECONDS
    app.api.ESCALATION_DELAY_SECONDS = 0.1  # 100ms for test

    try:
        await client.post(f"/shifts/{shift_id}/fanout")

        db_instance = get_db()
        fanout = db_instance.fanouts.get(shift_id)
        assert fanout.status == ShiftFanoutStatus.PENDING
        assert fanout.phone_call_sent_at is None

        # Wait for escalation to complete (delay + phone call time)
        # Poll until escalation happens
        for _ in range(50):  # 50 * 0.1s = 5s max wait
            await asyncio.sleep(0.1)
            fanout = db_instance.fanouts.get(shift_id)
            if fanout.status == ShiftFanoutStatus.ESCALATED:
                break

        fanout = db_instance.fanouts.get(shift_id)
        assert fanout.status == ShiftFanoutStatus.ESCALATED
        assert fanout.phone_call_sent_at is not None
    finally:
        app.api.ESCALATION_DELAY_SECONDS = original_delay


@pytest.mark.asyncio
async def test_escalation_skipped_if_claimed(client: AsyncClient) -> None:
    """Test that escalation doesn't happen if shift is already claimed."""
    import app.api

    shift_id = "f5a9d844-ecff-4f7a-8ef7-d091f22ad77e"

    # Use a short escalation delay for testing
    original_delay = app.api.ESCALATION_DELAY_SECONDS
    app.api.ESCALATION_DELAY_SECONDS = 0.2  # 200ms for test

    try:
        await client.post(f"/shifts/{shift_id}/fanout")

        # Claim the shift before escalation happens
        message = {"from_phone": "+15550001", "message": "yes"}
        await client.post("/messages/inbound", json=message)

        db_instance = get_db()
        fanout = db_instance.fanouts.get(shift_id)
        assert fanout.status == ShiftFanoutStatus.CLAIMED

        # Wait past the escalation delay
        await asyncio.sleep(0.5)

        # Verify escalation didn't happen since shift was claimed
        fanout = db_instance.fanouts.get(shift_id)
        assert fanout.status == ShiftFanoutStatus.CLAIMED
        assert fanout.phone_call_sent_at is None
    finally:
        app.api.ESCALATION_DELAY_SECONDS = original_delay


@pytest.mark.asyncio
async def test_multiple_caregivers_race_condition(client: AsyncClient) -> None:
    """Test that only one caregiver wins when multiple try to claim simultaneously."""
    shift_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    await client.post(f"/shifts/{shift_id}/fanout")

    message1 = {"from_phone": "+15550002", "message": "yes"}
    message2 = {"from_phone": "+15550003", "message": "yes"}

    responses = await asyncio.gather(
        client.post("/messages/inbound", json=message1),
        client.post("/messages/inbound", json=message2),
        return_exceptions=True,
    )

    claimed_count = 0
    already_claimed_count = 0

    for response in responses:
        if isinstance(response, Exception):
            continue
        status = response.json()["status"]
        if status == "shift_claimed":
            claimed_count += 1
        elif status == "shift_already_claimed":
            already_claimed_count += 1

    assert claimed_count == 1
    assert already_claimed_count == 1

    db_instance = get_db()
    fanout = db_instance.fanouts.get(shift_id)
    assert fanout.status == ShiftFanoutStatus.CLAIMED
    assert fanout.claimed_by in [
        "b7e6a0f4-4c32-44dd-8a6d-ec6b7e9477da",
        "c3d4e5f6-g7h8-9012-cdef-345678901234",
    ]
