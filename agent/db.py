"""OrderRepo — Oracle ADB (python-oracledb Thin async pool, wallet mTLS).

Membership lookup: short code (exact) -> phonetic+fuzzy name fallback (plan §10).
Orders persisted. Falls back to guest mode (db=None) if not configured / unreachable.
"""
from __future__ import annotations

import asyncio
import datetime
import logging

import oracledb

from .config import Settings
from .order_state import Member, OrderState

logger = logging.getLogger("voice-order")

_repo: "OrderRepo | None" = None
_lock = asyncio.Lock()


class OrderRepo:
    def __init__(self, s: Settings):
        self.s = s
        self.pool = None

    def connect(self) -> None:
        # create_pool_async is SYNC; connections are established lazily on acquire()
        self.pool = oracledb.create_pool_async(
            user=self.s.db_user, password=self.s.db_password, dsn=self.s.db_dsn,
            config_dir=self.s.wallet_dir, wallet_location=self.s.wallet_dir,
            wallet_password=self.s.wallet_password, min=1, max=4, increment=1,
        )

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    def _to_member(self, row) -> Member:
        num, mname, lang, dob, pron = row[0], row[1], row[2], row[3], row[4]
        today = datetime.date.today()
        is_bday = bool(dob and dob.month == today.month and dob.day == today.day)
        return Member(name=mname, membership_number=num, preferred_language=lang or "en",
                      is_birthday=is_bday, pronunciation=(pron or None))

    async def lookup_member(self, code: str | None = None, name: str | None = None) -> Member | None:
        async with self.pool.acquire() as con:
            cur = con.cursor()
            if code:
                await cur.execute(
                    "SELECT membership_number, member_name, preferred_language, date_of_birth, pronunciation "
                    "FROM voicedt.members WHERE membership_number = :c", c=code)
                row = await cur.fetchone()
                if row:
                    return self._to_member(row)
            if name:
                await cur.execute(
                    "SELECT membership_number, member_name, preferred_language, date_of_birth, pronunciation, "
                    "  CASE WHEN full_name_phonetic = PHONIC_ENCODE(DOUBLE_METAPHONE, :n) THEN 95 "
                    "       ELSE FUZZY_MATCH(JARO_WINKLER, member_name, :n) END AS score "
                    "FROM voicedt.members "
                    "WHERE full_name_phonetic = PHONIC_ENCODE(DOUBLE_METAPHONE, :n) "
                    "   OR FUZZY_MATCH(JARO_WINKLER, member_name, :n) >= 85 "
                    "ORDER BY score DESC FETCH FIRST 1 ROWS ONLY", n=name)
                row = await cur.fetchone()
                if row:
                    return self._to_member(row)
            return None

    async def save_order(self, order: OrderState, session_id: str = "") -> int | None:
        async with self.pool.acquire() as con:
            cur = con.cursor()
            member_id = None
            if order.member and order.member.membership_number:
                await cur.execute("SELECT member_id FROM voicedt.members WHERE membership_number = :c",
                                  c=order.member.membership_number)
                r = await cur.fetchone()
                member_id = r[0] if r else None
            oid = cur.var(oracledb.NUMBER)
            await cur.execute(
                "INSERT INTO voicedt.orders (session_id, member_id, order_status, total_price, confirmed_at) "
                "VALUES (:s, :m, 'confirmed', :t, SYSTIMESTAMP) RETURNING order_id INTO :oid",
                s=session_id, m=member_id, t=order.total, oid=oid)
            order_id = int(oid.getvalue()[0])
            for ln in order.lines:
                await cur.execute(
                    "INSERT INTO voicedt.order_items (order_id, item_id, item_name, size_name, modifiers, "
                    "  unit_price, quantity, discount_amount, discount_reason) "
                    "VALUES (:o,:iid,:nm,:sz,:mods,:up,:q,:dsc,:dr)",
                    o=order_id, iid=ln.item_id, nm=ln.name, sz=ln.size, mods=",".join(ln.modifiers),
                    up=ln.unit_price, q=ln.qty, dsc=ln.discount, dr=ln.discount_reason)
            await con.commit()
            return order_id

    async def create_member(self, name: str, code: str, pronunciation: str = "",
                            language: str = "en", dob_today: bool = True) -> None:
        async with self.pool.acquire() as con:
            cur = con.cursor()
            await cur.execute(
                "INSERT INTO voicedt.members (member_name, membership_number, pronunciation, "
                "  preferred_language, date_of_birth) VALUES (:nm,:c,:pr,:lang, "
                "  CASE WHEN :bday=1 THEN ADD_MONTHS(TRUNC(SYSDATE),-360) ELSE NULL END)",
                nm=name, c=code, pr=pronunciation, lang=language, bday=1 if dob_today else 0)
            await con.commit()


async def get_repo(s: Settings) -> "OrderRepo | None":
    """Shared repo singleton; returns None (guest mode) if DB not configured/unreachable."""
    global _repo
    if not (s.db_password and s.wallet_dir):
        return None
    async with _lock:
        if _repo is None:
            try:
                r = OrderRepo(s)
                r.connect()
                async with r.pool.acquire() as con:   # smoke the connection
                    cur = con.cursor()
                    await cur.execute("SELECT 1 FROM dual")
                    await cur.fetchone()
                _repo = r
                logger.info("ADB pool connected (voicedt)")
            except Exception:
                logger.exception("ADB connect failed — running guest mode")
                return None
    return _repo
