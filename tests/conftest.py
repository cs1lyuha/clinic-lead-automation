from datetime import datetime, timezone

import pytest

from leadflow import store
from leadflow.integrations import MemoryCRM, MemoryNotifier
from leadflow.normalize import LeadIn
from leadflow.pipeline import LeadPipeline

T0 = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def pipe():
    return LeadPipeline(store.connect(), MemoryCRM(), MemoryNotifier())


@pytest.fixture
def lead():
    counter = {"n": 0}

    def make(**kw):
        counter["n"] += 1
        data = {"delivery_id": f"d-{counter['n']:06d}", "name": "ana popescu", "phone": "069 123 456",
                "email": "ana@mail.md", "message": "Aș vrea o programare", "source": "website", "created_at": T0}
        data.update(kw)
        return LeadIn(**data)
    return make
