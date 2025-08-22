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
    item: str | None = None
    batch_no: str | None = None
    qty: int | None = None
    rack_location: str | None = None
    stock_status: str | None = None
    remark: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None

class UpdateReq(BaseModel):
    qty: int | None = None
    rack_location: str | None = None
    batch_no: str | None = None
    epc: str | None = None
    action: str = "WRITE_INFO"
    remark: str | None = None
    actor: str = "userA"
    device_id: str = "T1U-1"

class RegisterReq(BaseModel):
    tid: str
    epc: str
    label_number: str
    item: str
    qty: int
    batch_no: str | None = None
    rack_location: str | None = None
    stock_status: str = "IN_STOCK"
    remark: str | None = None
    actor: str = "userA"
    device_id: str = "T1U-1"

app = FastAPI()

@app.get("/health")
async def health():
    return {"ok": True}

# ---------- Helpers ----------
def _upper_or_none(s):
    return s.upper() if isinstance(s, str) and s.strip() != "" else None

def _row_to_tagrow(d: dict) -> TagRow:
    return TagRow(**d)

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
                    SELECT tid, epc, label_number, item, qty, batch_no, rack_location, stock_status,
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
                    SELECT tid, epc, label_number, item, qty, batch_no, rack_location, stock_status,
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
    # 预处理（大小写归一）
    epc_up   = _upper_or_none(body.epc)
    rack_up  = _upper_or_none(body.rack_location)
    batch_up = _upper_or_none(body.batch_no)
    # remark 不转大写
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
                    SELECT tid, epc, label_number, item, qty, batch_no, rack_location, stock_status
                    FROM rfid_tags_current WHERE UPPER(tid)=UPPER(%s) FOR UPDATE
                """, (tid,))
                old = cur.fetchone()
                if not old:
                    raise HTTPException(404, "not found")

                # 若改 EPC，检查是否被其他 TID 占用（不区分大小写）
                if epc_up:
                    cur.execute("""
                        SELECT tid FROM rfid_tags_current
                        WHERE UPPER(epc)=UPPER(%s) AND UPPER(tid)<>UPPER(%s) LIMIT 1
                    """, (epc_up, tid))
                    row = cur.fetchone()
                    if row:
                        raise HTTPException(409, "EPC already used by another tag")

                # 更新
                cur.execute("""
                    UPDATE rfid_tags_current
                    SET epc=COALESCE(%s, epc),
                        qty=COALESCE(%s, qty),
                        batch_no=COALESCE(%s, batch_no),
                        rack_location=COALESCE(%s, rack_location),
                        remark=COALESCE(%s, remark),
                        updated_at=NOW(), updated_by=%s
                    WHERE UPPER(tid)=UPPER(%s)
                """, (epc_up, body.qty, batch_up, rack_up, remark_v, body.actor, tid))

                # 日志
                epc_new = epc_up or old["epc"]
                log_batch_no = batch_up or old["batch_no"]
                cur.execute("""
                    INSERT INTO rfid_tags_log
                    (tid, epc, label_number, item, batch_no, action,
                     qty_old, qty_new, from_rack_location, to_rack_location,
                     stock_status_old, stock_status_new, remark,
                     updated_at, updated_by)
                    VALUES
                    (%s,%s,%s,%s,%s,%s,
                     %s,%s,%s,%s,
                     %s,%s,%s,
                     NOW(),%s)
                """, (
                    old["tid"], epc_new, old["label_number"], old["item"],
                    log_batch_no, body.action,
                    old["qty"], body.qty,
                    old["rack_location"], rack_up,
                    old["stock_status"], old["stock_status"],
                    remark_v, body.actor
                ))

                conn.commit()

                cur.execute("""
                    SELECT tid, epc, label_number, item, qty, batch_no, rack_location, stock_status,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current WHERE UPPER(tid)=UPPER(%s)
                """, (tid,))
                return cur.fetchone()
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

# ---------- Register (STRICT INSERT) ----------
@app.post("/tags/register", dependencies=[Depends(require_key)])
async def register(body: RegisterReq):
    # 正式入库前做大小写规范（remark 保持原状）
    tid_up    = body.tid.upper()
    epc_up    = body.epc.upper()
    label_up  = body.label_number.upper()
    item_up   = body.item.upper()
    batch_up  = _upper_or_none(body.batch_no)
    rack_up   = _upper_or_none(body.rack_location)
    remark_v  = body.remark

    pool = get_pool()
    def _work():
        conn = pool.get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            conn.start_transaction()
            cur = conn.cursor(dictionary=True)
            try:
                # TID/ EPC 唯一性依赖表的主键/唯一索引。此处直接插入由 DB 兜底。
                cur.execute("""
                    INSERT INTO rfid_tags_current
                    (tid, label_number, epc, item, qty, batch_no, rack_location, stock_status, remark, updated_at, updated_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s)
                """, (
                    tid_up, label_up, epc_up, item_up, body.qty,
                    batch_up, rack_up, body.stock_status, remark_v, body.actor
                ))

                # 写 REGISTER 日志
                cur.execute("""
                    INSERT INTO rfid_tags_log
                    (tid, epc, label_number, item, batch_no, action,
                     qty_old, qty_new, from_rack_location, to_rack_location,
                     stock_status_old, stock_status_new, remark,
                     updated_at, updated_by)
                    VALUES
                    (%s,%s,%s,%s,%s,'REGISTER',NULL,%s,NULL,%s,NULL,%s,%s,NOW(),%s)
                """, (
                    tid_up, epc_up, label_up, item_up, batch_up,
                    body.qty, rack_up, body.stock_status, remark_v, body.actor
                ))

                conn.commit()

                cur.execute("""
                    SELECT tid, epc, label_number, item, qty, batch_no, rack_location, stock_status,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current WHERE UPPER(tid)=UPPER(%s) LIMIT 1
                """, (tid_up,))
                return cur.fetchone()
            except sqlerr.IntegrityError:
                conn.rollback()
                # 返回冲突（例如重复 TID 或 EPC）
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
_SUGG_CACHE = {}  # key -> (expire_ts, list[str])
_SUGG_LOCK = threading.Lock()
_SUGG_TTL_SEC = int(os.getenv("BOM_SUGGEST_TTL", "300"))  # 5分钟

@app.get("/bom/items", dependencies=[Depends(require_key)])
async def bom_items(q: str = Query("", min_length=1), limit: int = 20):
    """在 bom 表中返回 item_cat='BATT' 且 item LIKE %q% 的 item（去重、全大写）"""
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




# 例：/bom/all-items-lite 仅返回 List[str]，只含 item 字段（已大写）
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
