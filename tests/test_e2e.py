import asyncio
import os
import uuid

import httpx
import pytest

BASE_URL = os.getenv("E2E_BASE_URL")
API_KEY = os.getenv("E2E_API_KEY", "dev-secret-key")
WEBHOOK_OK = "http://webhook-echo:8080/hooks/payments"
WEBHOOK_DOWN = "http://webhook-echo:9999/unreachable"

pytestmark = pytest.mark.skipif(not BASE_URL, reason="E2E_BASE_URL is not set")


@pytest.fixture
async def client():
    async with httpx.AsyncClient(base_url=BASE_URL, headers={"X-API-Key": API_KEY}, timeout=10) as c:
        yield c


def body(webhook_url: str = WEBHOOK_OK, amount: str = "100.00") -> dict:
    return {
        "amount": amount,
        "currency": "RUB",
        "description": "e2e",
        "metadata": {"test": True},
        "webhook_url": webhook_url,
    }


async def create(client: httpx.AsyncClient, payload: dict, key: str | None = None) -> httpx.Response:
    return await client.post("/api/v1/payments", json=payload, headers={"Idempotency-Key": key or str(uuid.uuid4())})


async def wait_for(client: httpx.AsyncClient, payment_id: str, predicate, timeout: float = 30) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        data = (await client.get(f"/api/v1/payments/{payment_id}")).json()
        if predicate(data):
            return data
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out, last state: {data}")
        await asyncio.sleep(0.5)


async def test_requires_api_key() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL) as c:
        assert (await c.get(f"/api/v1/payments/{uuid.uuid4()}")).status_code == 401
        r = await c.post("/api/v1/payments", json=body(), headers={"Idempotency-Key": "x", "X-API-Key": "wrong"})
        assert r.status_code == 401


async def test_requires_idempotency_key(client) -> None:
    assert (await client.post("/api/v1/payments", json=body())).status_code == 422


async def test_payment_is_processed_and_webhook_delivered(client) -> None:
    r = await create(client, body())
    assert r.status_code == 202
    created = r.json()
    assert created["status"] == "pending"

    done = await wait_for(client, created["payment_id"], lambda d: d["webhook_delivered_at"] is not None)
    assert done["status"] in {"succeeded", "failed"}
    assert done["processed_at"] is not None


async def test_idempotent_replay_and_conflict(client) -> None:
    key = str(uuid.uuid4())
    first = await create(client, body(), key)
    replay = await create(client, body(), key)
    assert replay.status_code == 202
    assert replay.headers.get("Idempotent-Replayed") == "true"
    assert replay.json()["payment_id"] == first.json()["payment_id"]

    conflict = await create(client, body(amount="1.00"), key)
    assert conflict.status_code == 409


async def test_concurrent_requests_with_same_key_create_one_payment(client) -> None:
    key = str(uuid.uuid4())
    responses = await asyncio.gather(*(create(client, body(), key) for _ in range(10)))
    assert {r.status_code for r in responses} == {202}
    assert len({r.json()["payment_id"] for r in responses}) == 1


async def test_unreachable_webhook_is_retried_then_dead_lettered(client) -> None:
    payment_id = (await create(client, body(WEBHOOK_DOWN))).json()["payment_id"]
    data = await wait_for(client, payment_id, lambda d: d["status"] != "pending")
    await asyncio.sleep(10)
    data = (await client.get(f"/api/v1/payments/{payment_id}")).json()
    assert data["webhook_delivered_at"] is None
