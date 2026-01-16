See [README.md](README.md) for instructions.

for this takehome i was tasked to design a microservice using python for sms and notifs 

when i was first recieved this takehome i took a look at the codebase and how it was structured and then read the readme.md file to familiarize myself with the contents in this project

after reading the intstructions i noticed that the first thing to do was to define the models. i looked at the sample_data.json to see what kind of data i was working with and created pydantic models for Shift, Caregiver, and ShiftFanout. the ShiftFanout model tracks the state of a fanout operation - whether its pending, claimed, or escalated to phone calls.

next i worked on the two main endpoints. for the fanout endpoint i made it so when you POST to /shifts/{shift_id}/fanout it looks up the shift, finds all caregivers with the matching role, and sends them an SMS. i also made sure to make it idempotent so if you call it twice it wont send duplicate messages - it just returns "already_fanout".

the inbound message endpoint was interesting because i had to figure out how to match an incoming message to the right shift. i used the intent classifier to determine if the message was an accept, decline, or unknown. if someone accepts i mark the shift as claimed and record who claimed it.

one thing i spent time on was the race condition handling. since multiple caregivers might try to claim the same shift at the same time, i used asyncio locks to make sure only one person can claim it. i created a lock per shift so different shifts dont block each other.

for the escalation logic i set up a background task that waits 10 minutes after the initial SMS fanout. if the shift is still pending after that time, it sends phone calls to all the caregivers. i made the delay configurable so tests can use a shorter delay instead of waiting 10 real minutes.

the tests were pretty straightforward - i tested the happy paths like successful fanout and claiming shifts, edge cases like unknown caregivers and no matching roles, and the race condition scenarios. the escalation tests were tricky at first because i tried using freezegun to manipulate time but it doesnt play nice with asyncio. ended up just using a configurable delay which worked better.

overall i think the design is pretty clean and handles the requirements well. the main tradeoff i made was using in-memory locks instead of something more robust like database locks, but the assumptions said we only have one instance running so it should be fine and that was given in the boilerplate code so i just continued with what i started with.