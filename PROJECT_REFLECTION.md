See [README.md](README.md) for instructions.

for this takehome i was tasked to design a microservice using python for sms and notifs 

when i was first recieved this takehome i took a look at the codebase and how it was structured and then read the readme.md file to familiarize myself with the contents in this project

after reading the intstructions i noticed that the first thing to do was to define the models. i looked at the sample_data.json to see what kind of data i was working with and created pydantic models for Shift, Caregiver, and ShiftFanout. the ShiftFanout model tracks the state of a fanout operation - whether its pending, claimed, or escalated to phone calls:

```python
class ShiftFanoutStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    ESCALATED = "escalated"

class ShiftFanout(BaseModel):
    shift_id: str
    status: ShiftFanoutStatus
    created_at: datetime
    claimed_by: str | None = None
    sms_sent_at: datetime | None = None
    phone_call_sent_at: datetime | None = None
    contacted_caregiver_ids: list[str] = []
```

next i worked on the two main endpoints. for the fanout endpoint i made it so when you POST to /shifts/{shift_id}/fanout it looks up the shift, finds all caregivers with the matching role, and sends them an SMS. i also made sure to make it idempotent so if you call it twice it wont send duplicate messages - it just returns "already_fanout":

```python
existing_fanout = db.fanouts.get(shift_id)
if existing_fanout is not None:
    return {
        "status": "already_fanout",
        "message": f"Fanout already initiated for shift {shift_id}",
    }
```

the inbound message endpoint was interesting because i had to figure out how to match an incoming message to the right shift. i used the intent classifier to determine if the message was an accept, decline, or unknown. if someone accepts i mark the shift as claimed and record who claimed it.

one thing i spent time on was the race condition handling. since multiple caregivers might try to claim the same shift at the same time, i used asyncio locks to make sure only one person can claim it. i created a lock per shift so different shifts dont block each other:

```python
_claim_locks: dict[str, asyncio.Lock] = {}

async def _get_claim_lock(shift_id: str) -> asyncio.Lock:
    async with _locks_lock:
        if shift_id not in _claim_locks:
            _claim_locks[shift_id] = asyncio.Lock()
        return _claim_locks[shift_id]

# when claiming a shift:
lock = await _get_claim_lock(fanout.shift_id)
async with lock:
    # check again inside the lock to prevent race conditions
    fanout = db.fanouts.get(fanout.shift_id)
    if fanout.status != ShiftFanoutStatus.PENDING:
        return {"status": "shift_already_claimed"}
    fanout.status = ShiftFanoutStatus.CLAIMED
```

for the escalation logic i set up a background task that waits 10 minutes after the initial SMS fanout. if the shift is still pending after that time, it sends phone calls to all the caregivers. i made the delay configurable so tests can use a shorter delay instead of waiting 10 real minutes:

```python
ESCALATION_DELAY_SECONDS = 600  # 10 minutes

async def _schedule_escalation(shift_id: str, created_at: datetime) -> None:
    await asyncio.sleep(ESCALATION_DELAY_SECONDS)
    
    fanout = db.fanouts.get(shift_id)
    if fanout is None or fanout.status != ShiftFanoutStatus.PENDING:
        return  # already claimed, dont escalate
    
    for caregiver in matching_caregivers:
        await place_phone_call(caregiver.phone, shift_message)
    
    fanout.status = ShiftFanoutStatus.ESCALATED
```

the tests were pretty straightforward - i tested the happy paths like successful fanout and claiming shifts, edge cases like unknown caregivers and no matching roles, and the race condition scenarios:

```python
# testing race condition - two caregivers try to claim at the same time
responses = await asyncio.gather(
    client.post("/messages/inbound", json=message1),
    client.post("/messages/inbound", json=message2),
)

# only one should succeed
assert claimed_count == 1
assert already_claimed_count == 1
```

the escalation tests were tricky at first because i tried using freezegun to manipulate time but it doesnt play nice with asyncio. ended up just using a configurable delay which worked better:

```python
# set a short delay for testing instead of waiting 10 real minutes
original_delay = app.api.ESCALATION_DELAY_SECONDS
app.api.ESCALATION_DELAY_SECONDS = 0.1  # 100ms for test

try:
    await client.post(f"/shifts/{shift_id}/fanout")
    
    # poll until escalation happens
    for _ in range(50):
        await asyncio.sleep(0.1)
        fanout = db_instance.fanouts.get(shift_id)
        if fanout.status == ShiftFanoutStatus.ESCALATED:
            break
    
    assert fanout.status == ShiftFanoutStatus.ESCALATED
finally:
    app.api.ESCALATION_DELAY_SECONDS = original_delay
```

for llm tools i used cursor with claude's opus 4.5 model to help me write the code faster. it was especially helpful for writing the tests and figuring out the asyncio stuff. when i ran into the freezegun issue with asyncio it helped me debug why the tests were hanging and come up with the configurable delay solution. i also used it to make sure i was handling all the edge cases properly.

also shoutout to uv - this was my first time using it and its so much faster than pip. running tests with `uv run pytest` just works without having to worry about virtual environments. definitely going to use it for future projects.

overall i think the design is pretty clean and handles the requirements well. the main tradeoff i made was using in-memory locks instead of something more robust like database locks, but the assumptions said we only have one instance running so it should be fine and that was given in the boilerplate code so i just continued with what i started with.