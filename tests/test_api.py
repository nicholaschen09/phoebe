from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from freezegun import freeze_time
from httpx import ASGITransport, AsyncClient

from app.api import create_app
from app.database import get_db, load_sample_data


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
    """Reset database before each test."""
    from app.database import _db

    global _db
    _db = None
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
