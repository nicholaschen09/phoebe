from __future__ import annotations

import json
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Generic, TypeVar

from app.models import Caregiver, Shift, ShiftFanout

K = TypeVar("K")
V = TypeVar("V")


class InMemoryKeyValueDatabase(Generic[K, V]):
    """
    Simple in-memory key/value database.
    """

    def __init__(self) -> None:
        self._store: MutableMapping[K, V] = {}

    def put(self, key: K, value: V) -> None:
        self._store[key] = value

    def get(self, key: K) -> V | None:
        return self._store.get(key)

    def delete(self, key: K) -> None:
        self._store.pop(key, None)

    def all(self) -> list[V]:
        return list(self._store.values())

    def clear(self) -> None:
        self._store.clear()

    def __iter__(self) -> Iterator[V]:
        return iter(self._store.values())

    def __len__(self) -> int:
        return len(self._store)


class Database:
    """Container for all database instances."""

    def __init__(self) -> None:
        self.shifts: InMemoryKeyValueDatabase[str, Shift] = (
            InMemoryKeyValueDatabase()
        )
        self.caregivers: InMemoryKeyValueDatabase[str, Caregiver] = (
            InMemoryKeyValueDatabase()
        )
        self.fanouts: InMemoryKeyValueDatabase[str, ShiftFanout] = (
            InMemoryKeyValueDatabase()
        )

    def get_caregivers_by_role(self, role: str) -> list[Caregiver]:
        """Get all caregivers with a specific role."""
        return [
            caregiver
            for caregiver in self.caregivers.all()
            if caregiver.role == role
        ]

    def get_caregiver_by_phone(self, phone: str) -> Caregiver | None:
        """Get a caregiver by their phone number."""
        for caregiver in self.caregivers.all():
            if caregiver.phone == phone:
                return caregiver
        return None


_db: Database | None = None


def get_db() -> Database:
    """Get the global database instance."""
    global _db
    if _db is None:
        _db = Database()
    return _db


def load_sample_data(db: Database | None = None) -> None:
    """Load sample data from sample_data.json into the database."""
    if db is None:
        db = get_db()

    sample_data_path = Path(__file__).parent.parent / "sample_data.json"
    with open(sample_data_path) as f:
        data = json.load(f)

    for caregiver_data in data["caregivers"]:
        caregiver = Caregiver(**caregiver_data)
        db.caregivers.put(caregiver.id, caregiver)

    for shift_data in data["shifts"]:
        shift = Shift(**shift_data)
        db.shifts.put(shift.id, shift)
