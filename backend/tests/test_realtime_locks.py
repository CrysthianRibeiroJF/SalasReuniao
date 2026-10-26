"""LockStore em memória e no Redis (fakeredis): mesma semântica, mesmos testes."""

from __future__ import annotations

import asyncio
from datetime import date, time, timedelta

import fakeredis
import pytest

from app.config import settings
from app.realtime.locks import utcnow
from app.realtime.memory import MemoryLockStore
from app.realtime.redis_store import EXPIRY_KEY, RedisLockStore, date_key, meta_key
from app.realtime.registry import LockStore, slot_key
from app.timeutil import local_dt

D = date(2026, 10, 26)  # segunda-feira
ROOM = 1
ANA, BIA, CAIO = (1, "Ana Recepção"), (2, "Bia Recepção"), (3, "Caio Recepção")


def t(hhmm: str) -> time:
    return time.fromisoformat(hhmm)


async def lock(store, who, start: str, end: str, *, room: int = ROOM, day: date = D, **kw):
    user_id, user_name = who
    return await store.acquire_detailed(
        room_id=room, date=day, start=t(start), end=t(end), user_id=user_id, user_name=user_name,
        **kw,
    )


@pytest.fixture
def fake_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
async def make_redis(fake_server):
    clients: list[fakeredis.FakeAsyncRedis] = []

    def _make() -> fakeredis.FakeAsyncRedis:
        client = fakeredis.FakeAsyncRedis(server=fake_server, decode_responses=True)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        await client.aclose()


@pytest.fixture(params=["memory", "redis"])
async def store(request, make_redis):
    if request.param == "memory":
        return MemoryLockStore()
    return RedisLockStore(make_redis())


@pytest.fixture
def short_ttl(monkeypatch):
    def _set(seconds: float) -> None:
        monkeypatch.setattr(settings, "lock_ttl_seconds", seconds)

    return _set


# ---------------------------------------------------------------------- ambos os backends


async def test_implements_protocol(store):
    assert isinstance(store, LockStore)


async def test_acquire_covers_every_slot(store):
    result = await lock(store, ANA, "14:00", "15:30", conn_id="c1")
    info = result.lock
    assert info is not None and result.conflicts == [] and result.replaced == []
    assert info.slot_keys == tuple(slot_key(ROOM, D, t(h)) for h in ("14:00", "14:30", "15:00"))
    assert (info.room_id, info.date, info.start, info.end) == (ROOM, D, t("14:00"), t("15:30"))
    assert (info.user_id, info.user_name, info.conn_id) == (1, "Ana Recepção", "c1")
    ttl = timedelta(seconds=settings.lock_ttl_seconds)
    assert utcnow() + ttl - timedelta(seconds=2) < info.expires_at <= utcnow() + ttl
    assert await store.get(info.lock_id) == info
    assert await store.list_active(D) == [info]


async def test_other_user_is_denied_on_any_shared_slot(store):
    first = (await lock(store, ANA, "14:00", "15:30")).lock
    denied = await lock(store, BIA, "15:00", "16:00")
    assert denied.lock is None
    assert [c.lock_id for c in denied.conflicts] == [first.lock_id]
    # adjacente ([15:30, 16:00) não toca [14:00, 15:30)) é permitido
    assert (await lock(store, BIA, "15:30", "16:00")).lock is not None


async def test_partial_conflict_rolls_back_everything(store):
    middle = (await lock(store, ANA, "15:00", "15:30")).lock
    denied = await lock(store, BIA, "14:00", "16:30")
    assert denied.lock is None and [c.lock_id for c in denied.conflicts] == [middle.lock_id]
    # nenhum slot da tentativa ficou preso: um terceiro pega as pontas
    assert (await lock(store, CAIO, "14:00", "15:00")).lock is not None
    assert (await lock(store, CAIO, "15:30", "16:30")).lock is not None
    assert {lk.user_id for lk in await store.list_active(D)} == {1, 3}


async def test_conflicts_are_detected_at_slot_granularity(store):
    """Travas guardam slots de slot_minutes: [14:00,14:10) e [14:20,14:40) disputam o slot 14:00."""
    await lock(store, ANA, "14:00", "14:10")
    assert (await lock(store, BIA, "14:20", "14:40")).lock is None


async def test_same_user_overlap_is_replaced(store):
    old = (await lock(store, ANA, "14:00", "15:00", conn_id="tab1")).lock
    other_day = (await lock(store, ANA, "09:00", "10:00")).lock
    result = await lock(store, ANA, "14:30", "16:00", conn_id="tab2")
    assert result.lock is not None and result.conflicts == []
    assert [r.lock_id for r in result.replaced] == [old.lock_id]
    assert await store.get(old.lock_id) is None
    active = {lk.lock_id for lk in await store.list_active(D)}
    assert active == {result.lock.lock_id, other_day.lock_id}
    # o slot 14:00 da trava substituída ficou livre
    assert (await lock(store, BIA, "14:00", "14:30")).lock is not None


async def test_release_is_owner_only(store):
    info = (await lock(store, ANA, "10:00", "11:00")).lock
    assert await store.release(info.lock_id, user_id=BIA[0]) is None
    assert await store.get(info.lock_id) is not None
    released = await store.release(info.lock_id, user_id=ANA[0])
    assert released is not None and released.lock_id == info.lock_id
    assert await store.release(info.lock_id, user_id=ANA[0]) is None
    assert await store.list_active() == []
    assert (await lock(store, BIA, "10:00", "11:00")).lock is not None


async def test_release_without_user_is_allowed(store):
    """Serviço liberando a trava ao salvar a reserva (sem checagem de dono)."""
    info = (await lock(store, ANA, "10:00", "11:00")).lock
    assert (await store.release(info.lock_id)).lock_id == info.lock_id
    assert await store.release("nao-existe") is None


async def test_refresh_extends_ttl_for_owner_only(store, short_ttl):
    short_ttl(1)
    info = (await lock(store, ANA, "10:00", "11:00")).lock
    short_ttl(60)
    assert await store.refresh(info.lock_id, user_id=BIA[0]) is None
    refreshed = await store.refresh(info.lock_id, user_id=ANA[0])
    assert refreshed is not None and refreshed.expires_at > info.expires_at + timedelta(seconds=30)
    assert (await store.get(info.lock_id)).expires_at == refreshed.expires_at
    await asyncio.sleep(1.2)  # passou do TTL original
    assert await store.get(info.lock_id) is not None
    assert await store.pop_expired() == []
    assert (await lock(store, BIA, "10:00", "10:30")).lock is None
    assert await store.refresh("nao-existe", user_id=ANA[0]) is None


async def test_expired_lock_is_ignored_and_popped_once(store, short_ttl):
    short_ttl(0.3)
    old = (await lock(store, ANA, "10:00", "11:00")).lock
    await asyncio.sleep(0.45)
    assert await store.get(old.lock_id) is None
    assert await store.list_active() == [] and await store.list_active(D) == []
    assert await store.find_overlapping(ROOM, local_dt(D, t("10:00")), local_dt(D, t("11:00"))) == []
    assert await store.refresh(old.lock_id, user_id=ANA[0]) is None
    # outra pessoa pode travar antes mesmo da varredura
    short_ttl(60)
    new = (await lock(store, BIA, "10:30", "11:30")).lock
    assert new is not None
    popped = await store.pop_expired()
    assert [p.lock_id for p in popped] == [old.lock_id]
    assert await store.pop_expired() == []
    # a limpeza da expirada não apagou o slot 10:30 que agora é da Bia
    assert await store.get(new.lock_id) == new
    assert (await lock(store, CAIO, "10:30", "11:00")).lock is None
    assert (await lock(store, CAIO, "10:00", "10:30")).lock is not None


async def test_find_overlapping_is_half_open(store):
    info = (await lock(store, ANA, "14:00", "15:00")).lock

    async def overlapping(start: str, end: str, *, room: int = ROOM, day: date = D):
        found = await store.find_overlapping(room, local_dt(day, t(start)), local_dt(day, t(end)))
        return [lk.lock_id for lk in found]

    assert await overlapping("15:00", "16:00") == []
    assert await overlapping("13:00", "14:00") == []
    assert await overlapping("14:55", "15:30") == [info.lock_id]
    assert await overlapping("13:00", "14:05") == [info.lock_id]
    assert await overlapping("14:15", "14:20") == [info.lock_id]
    assert await overlapping("08:00", "19:00") == [info.lock_id]
    assert await overlapping("14:00", "15:00", room=2) == []
    assert await overlapping("14:00", "15:00", day=D + timedelta(days=1)) == []
    assert await overlapping("14:30", "14:30") == []  # intervalo vazio


async def test_list_active_filters_by_date_and_sorts(store):
    tue = D + timedelta(days=1)
    b = (await lock(store, ANA, "14:00", "15:00", room=2)).lock
    a = (await lock(store, BIA, "09:00", "10:00", room=2)).lock
    c = (await lock(store, ANA, "08:00", "09:00", room=1, day=tue)).lock
    d = (await lock(store, CAIO, "16:00", "17:00", room=1)).lock
    assert [lk.lock_id for lk in await store.list_active(D)] == [d.lock_id, a.lock_id, b.lock_id]
    assert [lk.lock_id for lk in await store.list_active(tue)] == [c.lock_id]
    assert [lk.lock_id for lk in await store.list_active()] == [
        d.lock_id, a.lock_id, b.lock_id, c.lock_id,
    ]


async def test_release_connection(store):
    a = (await lock(store, ANA, "09:00", "10:00", conn_id="c1")).lock
    b = (await lock(store, ANA, "14:00", "15:00", room=2, conn_id="c1")).lock
    kept = (await lock(store, ANA, "11:00", "12:00", conn_id="c2")).lock
    released = await store.release_connection("c1")
    assert sorted(lk.lock_id for lk in released) == sorted([a.lock_id, b.lock_id])
    assert await store.release_connection("c1") == []
    assert [lk.lock_id for lk in await store.list_active()] == [kept.lock_id]
    assert (await lock(store, BIA, "09:00", "10:00")).lock is not None


async def test_booking_id_is_kept(store):
    info = (await lock(store, ANA, "09:00", "10:00", booking_id="b-123")).lock
    assert (await store.get(info.lock_id)).booking_id == "b-123"
    assert info.to_out().booking_id == "b-123"


async def test_empty_interval_is_rejected(store):
    with pytest.raises(ValueError):
        await lock(store, ANA, "10:00", "10:00")


# ---------------------------------------------------------------------- só Redis


async def test_redis_store_requires_decoded_responses(fake_server):
    raw = fakeredis.FakeAsyncRedis(server=fake_server)
    try:
        with pytest.raises(ValueError):
            RedisLockStore(raw)
    finally:
        await raw.aclose()


async def test_redis_keys_layout_and_cleanup(make_redis):
    r = make_redis()
    store = RedisLockStore(r)
    info = (await lock(store, ANA, "14:00", "15:00", conn_id="c1")).lock
    for key in info.slot_keys:
        assert await r.get(key) == info.lock_id
    # metadados vivem mais que os slots (a varredura ainda consegue descrever a trava)
    assert await r.pttl(meta_key(info.lock_id)) > max([await r.pttl(k) for k in info.slot_keys])
    assert await r.zscore(EXPIRY_KEY, info.lock_id) == pytest.approx(
        info.expires_at.timestamp() * 1000, abs=1
    )
    assert await r.smembers(date_key(D)) == {info.lock_id}
    assert await r.smembers("locks:conn:c1") == {info.lock_id}

    await store.release(info.lock_id, user_id=ANA[0])
    assert await r.keys("*") == []


async def test_redis_rollback_leaves_no_keys(make_redis):
    r = make_redis()
    store = RedisLockStore(r)
    middle = (await lock(store, ANA, "15:00", "15:30")).lock
    assert (await lock(store, BIA, "14:00", "16:30")).lock is None
    expected = {*middle.slot_keys, meta_key(middle.lock_id), EXPIRY_KEY, date_key(D)}
    assert set(await r.keys("*")) == expected


async def test_redis_two_instances_share_locks(make_redis):
    a, b = RedisLockStore(make_redis()), RedisLockStore(make_redis())
    info = (await lock(a, ANA, "14:00", "15:00")).lock
    assert [lk.lock_id for lk in await b.list_active(D)] == [info.lock_id]
    found = await b.find_overlapping(ROOM, local_dt(D, t("14:30")), local_dt(D, t("16:00")))
    assert [lk.lock_id for lk in found] == [info.lock_id]
    denied = await lock(b, BIA, "14:30", "15:30")
    assert denied.lock is None and [c.lock_id for c in denied.conflicts] == [info.lock_id]
    replaced = await lock(b, ANA, "14:30", "15:30")  # mesma usuária em outra instância
    assert [r.lock_id for r in replaced.replaced] == [info.lock_id]
    assert await a.get(info.lock_id) is None


async def test_redis_pop_expired_exactly_once_across_instances(make_redis, short_ttl):
    r = make_redis()
    a, b = RedisLockStore(r), RedisLockStore(make_redis())
    short_ttl(0.3)
    created = set()
    for room in range(1, 11):
        for start, end in (("08:00", "09:00"), ("10:00", "11:30")):
            created.add((await lock(a if room % 2 else b, ANA, start, end, room=room)).lock.lock_id)
    await asyncio.sleep(0.45)
    seen_a: list[str] = []
    seen_b: list[str] = []
    for _ in range(3):
        got_a, got_b = await asyncio.gather(a.pop_expired(), b.pop_expired())
        seen_a += [lk.lock_id for lk in got_a]
        seen_b += [lk.lock_id for lk in got_b]
    assert not set(seen_a) & set(seen_b)
    assert sorted(seen_a + seen_b) == sorted(created)
    assert await r.keys("*") == []


async def test_redis_release_races_with_sweeper(make_redis, short_ttl):
    a, b = RedisLockStore(make_redis()), RedisLockStore(make_redis())
    short_ttl(0.2)
    infos = [(await lock(a, ANA, "08:00", "09:00", room=room)).lock for room in range(1, 6)]
    await asyncio.sleep(0.3)
    releases = [a.release(info.lock_id, user_id=ANA[0]) for info in infos]
    results = await asyncio.gather(b.pop_expired(), *releases)
    swept = {lk.lock_id for lk in results[0]}
    released = {lk.lock_id for lk in results[1:] if lk is not None}
    assert not swept & released
    assert swept | released == {info.lock_id for info in infos}


async def test_redis_orphan_slot_key_is_reclaimed(make_redis):
    r = make_redis()
    store = RedisLockStore(r)
    ghost = slot_key(ROOM, D, t("09:00"))
    await r.set(ghost, "fantasma", px=60_000)  # sem metadados (ex.: processo morreu no meio)
    result = await lock(store, BIA, "09:00", "10:00")
    assert result.lock is not None
    assert await r.get(ghost) == result.lock.lock_id


async def test_redis_concurrent_acquire_single_winner(make_redis):
    stores = [RedisLockStore(make_redis()) for _ in range(5)]
    users = [(i, f"Usuária {i}") for i in range(1, 6)]
    results = await asyncio.gather(
        *(lock(s, u, "14:00", "16:00") for s, u in zip(stores, users))
    )
    winners = [r.lock for r in results if r.lock is not None]
    assert len(winners) == 1
    assert len(await stores[0].list_active(D)) == 1
