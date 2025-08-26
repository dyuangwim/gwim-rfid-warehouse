# main.py
import os, asyncio, time, threading
from fastapi import FastAPI, HTTPException, Depends, Header, Query
from pydantic import BaseModel
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import pooling
from mysql.connector import errors as sqlerr

load_dotenv()

# ---------- MySQL Pool ----------
_POOL = None
def get_pool():
    global _POOL
    if _POOL is None:
        _POOL = pooling.MySQLConnectionPool(
            pool_name="rfid_pool",
            pool_size=10,
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", "3306")),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
            autocommit=True,
            connection_timeout=int(os.getenv("CONNECT_TIMEOUT", "3")),
            pool_reset_session=True
        )
    return _POOL

# ---------- Auth ----------
API_KEY = os.getenv("API_KEY", "changeme")
async def require_key(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(401, "Unauthorized")

# ---------- Schemas ----------
class TagRow(BaseModel):
    tid: str
    epc: str | None = None
    label_number: str | None = None
    muf_no: str | None = None
    item: str | None = None
    batch_no: str | None = None
    qty: int | None = None
    rack_location: str | None = None
    area: str | None = None
    remark: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None

class UpdateReq(BaseModel):
    qty: int | None = None
    rack_location: str | None = None  # "" 表示清空，null 表示不改
    batch_no: str | None = None
    item: str | None = None
    area: str | None = None
    epc: str | None = None
    label_number: str | None = None
    muf_no: str | None = None
    action: str = "WRITE_INFO"
    remark: str | None = None
    actor: str = "userA"
    device_id: str = "T1U-1"

class RegisterReq(BaseModel):
    tid: str
    epc: str
    label_number: str
    muf_no: str | None = None
    item: str
    qty: int
    batch_no: str | None = None
    rack_location: str | None = None
    area: str = "W/H"
    remark: str | None = None
    actor: str = "userA"
    device_id: str = "T1U-1"

# 需要库位的区域（与前端、DB 枚举一致）
REQUIRED_RACK_AREAS = {"W/H", "KITING"}

app = FastAPI()

@app.get("/health")
async def health():
    return {"ok": True}

# ---------- Helpers ----------
def _upper_or_none(s):
    return s.upper() if isinstance(s, str) and s.strip() != "" else None

# ---------- Query (case-insensitive) ----------
@app.get("/tags/by-epc", dependencies=[Depends(require_key)])
async def by_epc(epc: str):
    pool = get_pool()
    def _work():
        conn = pool.get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute("""
                    SELECT tid, epc, label_number, muf_no, item, qty, batch_no, rack_location, area,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current WHERE UPPER(epc)=UPPER(%s) LIMIT 1
                """, (epc,))
                return cur.fetchone()
            finally:
                cur.close()
        finally:
            conn.close()
    row = await asyncio.wait_for(asyncio.to_thread(_work), timeout=int(os.getenv("READ_TIMEOUT", "3")))
    if not row:
        raise HTTPException(404, "not found")
    return row

@app.get("/tags/by-tid", dependencies=[Depends(require_key)])
async def by_tid(tid: str):
    pool = get_pool()
    def _work():
        conn = pool.get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute("""
                    SELECT tid, epc, label_number, muf_no, item, qty, batch_no, rack_location, area,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current WHERE UPPER(tid)=UPPER(%s) LIMIT 1
                """, (tid,))
                return cur.fetchone()
            finally:
                cur.close()
        finally:
            conn.close()
    row = await asyncio.wait_for(asyncio.to_thread(_work), timeout=int(os.getenv("READ_TIMEOUT", "3")))
    if not row:
        raise HTTPException(404, "not found")
    return row

# ---------- Update by TID (normalize to UPPER except remark) ----------
@app.patch("/tags/{tid}", dependencies=[Depends(require_key)])
async def update_by_tid(tid: str, body: UpdateReq):
    epc_up   = _upper_or_none(body.epc)
    rack_raw = body.rack_location if isinstance(body.rack_location, str) else None  # ""=清空, None=不改
    rack_up  = (rack_raw.strip().upper() if isinstance(rack_raw, str) and rack_raw.strip() != "" else None)
    batch_up = _upper_or_none(body.batch_no)
    item_up  = _upper_or_none(body.item)
    area_up  = _upper_or_none(body.area)
    label_up = _upper_or_none(body.label_number)
    muf_up   = _upper_or_none(body.muf_no)
    remark_v = body.remark

    pool = get_pool()
    def _work():
        conn = pool.get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            conn.start_transaction()
            cur = conn.cursor(dictionary=True)
            try:
                # 锁行
                cur.execute("""
                    SELECT tid, epc, label_number, muf_no, item, qty, batch_no, rack_location, area
                    FROM rfid_tags_current WHERE UPPER(tid)=UPPER(%s) FOR UPDATE
                """, (tid,))
                old = cur.fetchone()
                if not old:
                    raise HTTPException(404, "not found")

                # 计算“更新后的最终值”（用于必填校验）
                final_area = (area_up or old["area"])
                if rack_raw is None:
                    final_rack = old["rack_location"]
                else:
                    final_rack = None if rack_raw == "" else rack_up

                # 若目标区域需要库位，但最终库位为空 → 拒绝
                if final_area in REQUIRED_RACK_AREAS and (final_rack is None or (isinstance(final_rack, str) and final_rack.strip() == "")):
                    raise HTTPException(400, f"rack_location required for area {final_area}")

                # EPC 冲突检查
                if epc_up:
                    cur.execute("""
                        SELECT tid FROM rfid_tags_current
                        WHERE UPPER(epc)=UPPER(%s) AND UPPER(tid)<>UPPER(%s) LIMIT 1
                    """, (epc_up, tid))
                    if cur.fetchone():
                        raise HTTPException(409, "EPC already used by another tag")

                # ====== 更新（空串清空 / None 不变）======
                cur.execute("""
                    UPDATE rfid_tags_current
                    SET epc=COALESCE(%s, epc),
                        label_number=COALESCE(%s, label_number),
                        muf_no=COALESCE(%s, muf_no),
                        item=COALESCE(%s, item),
                        qty=COALESCE(%s, qty),
                        batch_no=COALESCE(%s, batch_no),
                        rack_location=CASE WHEN %s = '' THEN NULL ELSE COALESCE(%s, rack_location) END,
                        area=COALESCE(%s, area),
                        remark=COALESCE(%s, remark),
                        updated_at=NOW(), updated_by=%s
                    WHERE UPPER(tid)=UPPER(%s)
                """, (
                    epc_up, label_up, muf_up, item_up, body.qty, batch_up,
                    (rack_raw or ""),   # 清空判定（空串 → NULL）
                    rack_up,            # 非空值则更新；None 表示不改
                    area_up, remark_v, body.actor, tid
                ))

                # 取新值用于日志/动作
                cur.execute("""
                    SELECT tid, epc, label_number, muf_no, item, qty, batch_no, rack_location, area
                    FROM rfid_tags_current WHERE UPPER(tid)=UPPER(%s)
                """, (tid,))
                new = cur.fetchone()

                # 动作归类：若仅位置变化且数量没改 → MOVE
                qty_changed   = (body.qty is not None and body.qty != old["qty"])
                area_changed  = (area_up is not None and new["area"] != old["area"])
                rack_changed  = (new["area"] in REQUIRED_RACK_AREAS and new["rack_location"] != old["rack_location"])

                final_action = body.action
                if not qty_changed and (area_changed or rack_changed) and body.action != "ADJUST_QTY":
                    final_action = "MOVE"

                # 日志
                cur.execute("""
                    INSERT INTO rfid_tags_log
                    (tid, epc, label_number, item, batch_no, action,
                     qty_old, qty_new, from_rack_location, to_rack_location,
                     area_old, area_new, remark, updated_at, updated_by)
                    VALUES
                    (%s,%s,%s,%s,%s,%s,
                     %s,%s,%s,%s,
                     %s,%s,%s,NOW(),%s)
                """, (
                    old["tid"],
                    new["epc"], new["label_number"], new["item"], new["batch_no"],
                    final_action,
                    old["qty"], new["qty"],
                    old["rack_location"], new["rack_location"],
                    old["area"], new["area"],
                    remark_v, body.actor
                ))

                conn.commit()
                return new
            except:
                conn.rollback(); raise
            finally:
                cur.close()
        finally:
            conn.close()

    row = await asyncio.wait_for(asyncio.to_thread(_work), timeout=int(os.getenv("WRITE_TIMEOUT", "4")))
    if not row:
        raise HTTPException(404, "not found")
    return row

# ---------- Register (STRICT INSERT) ----------
@app.post("/tags/register", dependencies=[Depends(require_key)])
async def register(body: RegisterReq):
    tid_up    = body.tid.upper()
    epc_up    = body.epc.upper()
    label_up  = body.label_number.upper()
    item_up   = body.item.upper()
    batch_up  = _upper_or_none(body.batch_no)
    rack_up   = _upper_or_none(body.rack_location)
    muf_up    = _upper_or_none(body.muf_no)
    remark_v  = body.remark
    area_up   = _upper_or_none(body.area) or "W/H"

    # 注册时也做必填校验
    if area_up in REQUIRED_RACK_AREAS and (rack_up is None or rack_up.strip() == ""):
        raise HTTPException(400, f"rack_location required for area {area_up}")

    pool = get_pool()
    def _work():
        conn = pool.get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            conn.start_transaction()
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute("""
                    INSERT INTO rfid_tags_current
                    (tid, label_number, epc, muf_no, item, qty, batch_no, rack_location, area, remark, updated_at, updated_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s)
                """, (tid_up, label_up, epc_up, muf_up, item_up, body.qty, batch_up, rack_up, area_up, remark_v, body.actor))

                cur.execute("""
                    INSERT INTO rfid_tags_log
                    (tid, epc, label_number, item, batch_no, action,
                     qty_old, qty_new, from_rack_location, to_rack_location,
                     area_old, area_new, remark, updated_at, updated_by)
                    VALUES
                    (%s,%s,%s,%s,%s,'REGISTER',NULL,%s,NULL,%s,NULL,%s,%s,NOW(),%s)
                """, (tid_up, epc_up, label_up, item_up, batch_up,
                      body.qty, rack_up, area_up, remark_v, body.actor))

                conn.commit()

                cur.execute("""
                    SELECT tid, epc, label_number, muf_no, item, qty, batch_no, rack_location, area,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current WHERE UPPER(tid)=UPPER(%s) LIMIT 1
                """, (tid_up,))
                return cur.fetchone()
            except sqlerr.IntegrityError:
                conn.rollback()
                raise HTTPException(409, "Duplicate tid/epc")
            except:
                conn.rollback()
                raise
            finally:
                cur.close()
        finally:
            conn.close()

    row = await asyncio.wait_for(asyncio.to_thread(_work), timeout=int(os.getenv("WRITE_TIMEOUT", "4")))
    if not row:
        raise HTTPException(404, "not found")
    return row

# ---------- /bom/items 自动完成（含缓存） ----------
_SUGG_CACHE = {}
_SUGG_LOCK = threading.Lock()
_SUGG_TTL_SEC = int(os.getenv("BOM_SUGGEST_TTL", "300"))

@app.get("/bom/items", dependencies=[Depends(require_key)])
async def bom_items(q: str = Query("", min_length=1), limit: int = 20):
    key = (q.upper(), int(limit))
    now = time.time()
    with _SUGG_LOCK:
        ent = _SUGG_CACHE.get(key)
        if ent and ent[0] > now:
            return ent[1]

    pool = get_pool()
    def _work():
        conn = pool.get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            cur = conn.cursor()
            try:
                cur.execute("""
                    SELECT DISTINCT UPPER(item) AS item
                    FROM bom
                    WHERE item_cat='BATT' AND UPPER(item) LIKE CONCAT('%', UPPER(%s), '%')
                    ORDER BY item
                    LIMIT %s
                """, (q, limit))
                return [row[0] for row in cur.fetchall()]
            finally:
                cur.close()
        finally:
            conn.close()

    rows = await asyncio.wait_for(asyncio.to_thread(_work), timeout=int(os.getenv("READ_TIMEOUT", "3")))
    with _SUGG_LOCK:
        _SUGG_CACHE[key] = (now + _SUGG_TTL_SEC, rows)
    return rows

@app.get("/bom/all-items-lite", dependencies=[Depends(require_key)])
async def bom_all_items_lite():
    pool = get_pool()
    def _work():
        conn = pool.get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            cur = conn.cursor()
            try:
                cur.execute("""
                    SELECT DISTINCT UPPER(item) AS item
                    FROM bom
                    WHERE item_cat='BATT' AND item IS NOT NULL AND item <> ''
                """)
                return [row[0] for row in cur.fetchall()]
            finally:
                cur.close()
        finally:
            conn.close()
    return await asyncio.wait_for(asyncio.to_thread(_work), timeout=int(os.getenv("READ_TIMEOUT", "3")))

# ---------- 下行增量接口（稳定分页：updated_at + tid） ----------
@app.get("/tags/updated-since", dependencies=[Depends(require_key)])
async def tags_updated_since(
    ts: str = Query("1970-01-01 00:00:00"),
    last_tid: str = Query("", description="与 ts 同秒时的游标 TID"),
    limit: int = 500
):
    pool = get_pool()
    def _work():
        conn = pool.get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute("""
                    SELECT tid, epc, label_number, muf_no, item, qty, batch_no, rack_location, area,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current
                    WHERE (updated_at > %s)
                       OR (updated_at = %s AND tid > %s)
                    ORDER BY updated_at ASC, tid ASC
                    LIMIT %s
                """, (ts, ts, last_tid, int(limit)))
                return cur.fetchall()
            finally:
                cur.close()
        finally:
            conn.close()
    rows = await asyncio.wait_for(asyncio.to_thread(_work), timeout=int(os.getenv("READ_TIMEOUT", "4")))
    return rows
