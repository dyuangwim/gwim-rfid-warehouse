# main.py
import os, asyncio, time, threading
from fastapi import FastAPI, HTTPException, Depends, Header, Query
from pydantic import BaseModel
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import pooling
from mysql.connector import errors as sqlerr
from datetime import datetime, date

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
    rack_location: str | None = None  # "" 表示清空，None 表示不改
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
    # 乐观并发：客户端视图的旧时间戳
    prev_updated_at: str | None = None

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

# NEW: Audit req/resp
class AuditReq(BaseModel):
    tid: str | None = None
    epc: str | None = None
    qty: int | None = None
    rack_location: str | None = None   # None=不改, ""=清空, 其他=更新
    remark: str | None = None
    actor: str = "auditor"
    device_id: str = "T1U-1"

class AuditResp(BaseModel):
    audit_id: int
    result: str               # "NO_CHANGES" / "CHANGED"
    change_notes: str | None
    latest: TagRow | None

# 需要库位的区域（与前端一致）
REQUIRED_RACK_AREAS = {"W/H", "KITING"}

app = FastAPI()

@app.get("/health")
async def health():
    return {"ok": True}

# ---------- Helpers ----------
def _upper_or_none(s):
    return s.upper() if isinstance(s, str) and s.strip() != "" else None

def _normalize_row(row: dict | None):
    """把 updated_at/updated_by 统一格式，避免前端误判并发冲突"""
    if not row:
        return row
    ts = row.get("updated_at")
    if isinstance(ts, (datetime, date)):
        row["updated_at"] = ts.strftime("%Y-%m-%d %H:%M:%S")
    elif ts is not None:
        row["updated_at"] = str(ts)
    ub = row.get("updated_by")
    if ub is not None:
        row["updated_by"] = str(ub)
    return row

def _normalize_rows(rows: list[dict]):
    for r in rows:
        _normalize_row(r)
    return rows

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
                row = cur.fetchone()
                return _normalize_row(row)
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
                row = cur.fetchone()
                return _normalize_row(row)
            finally:
                cur.close()
        finally:
            conn.close()
    row = await asyncio.wait_for(asyncio.to_thread(_work), timeout=int(os.getenv("READ_TIMEOUT", "3")))
    if not row:
        raise HTTPException(404, "not found")
    return row

# ---------- Update by TID ----------
@app.patch("/tags/{tid}", dependencies=[Depends(require_key)])
async def update_by_tid(tid: str, body: UpdateReq):
    epc_up   = _upper_or_none(body.epc)
    rack_raw = body.rack_location if isinstance(body.rack_location, str) else None
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
                # 锁行（包含 updated_at）
                cur.execute("""
                    SELECT tid, epc, label_number, muf_no, item, qty, batch_no, rack_location, area, updated_at
                    FROM rfid_tags_current WHERE UPPER(tid)=UPPER(%s) FOR UPDATE
                """, (tid,))
                old = cur.fetchone()
                if not old:
                    raise HTTPException(404, "not found")

                # 并发冲突（乐观）
                if body.prev_updated_at is not None:
                    old_ts = old["updated_at"]
                    if isinstance(old_ts, (datetime, date)):
                        old_ts = old_ts.strftime("%Y-%m-%d %H:%M:%S")
                    if str(old_ts) != str(body.prev_updated_at):
                        raise HTTPException(409, "conflict: the tag was updated by someone else")

                # 最终值 & 校验
                final_area = (area_up or old["area"])
                if rack_raw is None:
                    final_rack = old["rack_location"]
                else:
                    final_rack = None if rack_raw == "" else rack_up

                if final_area in REQUIRED_RACK_AREAS and (final_rack is None or (isinstance(final_rack, str) and final_rack.strip() == "")):
                    raise HTTPException(400, f"rack_location required for area {final_area}")

                # EPC唯一性
                if epc_up:
                    cur.execute("""
                        SELECT tid FROM rfid_tags_current
                        WHERE UPPER(epc)=UPPER(%s) AND UPPER(tid)<>UPPER(%s) LIMIT 1
                    """, (epc_up, tid))
                    if cur.fetchone():
                        raise HTTPException(409, "EPC already used by another tag")

                # UPDATE
                cur.execute("""
                    UPDATE rfid_tags_current
                    SET epc=COALESCE(%s, epc),
                        label_number=COALESCE(%s, label_number),
                        muf_no=COALESCE(%s, muf_no),
                        item=COALESCE(%s, item),
                        qty=COALESCE(%s, qty),
                        batch_no=COALESCE(%s, batch_no),
                        rack_location = CASE
                            WHEN %s IS NULL THEN rack_location
                            WHEN %s = '' THEN NULL
                            ELSE %s
                        END,
                        area=COALESCE(%s, area),
                        remark=COALESCE(%s, remark),
                        updated_at=NOW(), updated_by=%s
                    WHERE UPPER(tid)=UPPER(%s)
                """, (
                    epc_up, label_up, muf_up, item_up, body.qty, batch_up,
                    rack_raw, rack_raw, rack_up,
                    area_up, remark_v, body.actor, tid
                ))

                # 取新值
                cur.execute("""
                    SELECT tid, epc, label_number, muf_no, item, qty, batch_no, rack_location, area,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current WHERE UPPER(tid)=UPPER(%s)
                """, (tid,))
                new = cur.fetchone()
                new = _normalize_row(new)

                # 动作归类 & 日志
                qty_changed   = (body.qty is not None and body.qty != old["qty"])
                area_changed  = (area_up is not None and new["area"] != old["area"])
                rack_changed  = (new["area"] in REQUIRED_RACK_AREAS and new["rack_location"] != old["rack_location"])
                final_action = body.action
                if not qty_changed and (area_changed or rack_changed) and body.action != "ADJUST_QTY":
                    final_action = "MOVE"

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
                    old["tid"], new["epc"], new["label_number"], new["item"], new["batch_no"],
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
                """, (tid_up, epc_up, label_up, item_up, batch_up, body.qty, rack_up, area_up, remark_v, body.actor))

                conn.commit()

                cur.execute("""
                    SELECT tid, epc, label_number, muf_no, item, qty, batch_no, rack_location, area,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current WHERE UPPER(tid)=UPPER(%s) LIMIT 1
                """, (tid_up,))
                row = cur.fetchone()
                return _normalize_row(row)
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
                rows = cur.fetchall()
                return _normalize_rows(rows)
            finally:
                cur.close()
        finally:
            conn.close()
    rows = await asyncio.wait_for(asyncio.to_thread(_work), timeout=int(os.getenv("READ_TIMEOUT", "4")))
    return rows

# ---------- NEW: AUDIT ----------
@app.post("/audit/verify-save", dependencies=[Depends(require_key)])
async def audit_verify_save(body: AuditReq) -> AuditResp:
    pool = get_pool()

    def _work():
        conn = pool.get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            conn.start_transaction()
            cur = conn.cursor(dictionary=True)
            try:
                # 1) by-epc → tid
                tid = body.tid
                if (tid is None or tid.strip() == "") and body.epc:
                    cur.execute("SELECT tid FROM rfid_tags_current WHERE UPPER(epc)=UPPER(%s) LIMIT 1", (body.epc,))
                    row = cur.fetchone()
                    if not row:
                        raise HTTPException(404, "not found")
                    tid = row["tid"]
                if tid is None or tid.strip() == "":
                    raise HTTPException(400, "tid or epc required")

                # 2) 锁当前行
                cur.execute("""
                    SELECT tid, epc, label_number, muf_no, item, qty, batch_no, rack_location, area
                    FROM rfid_tags_current WHERE UPPER(tid)=UPPER(%s) FOR UPDATE
                """, (tid,))
                old = cur.fetchone()
                if not old:
                    raise HTTPException(404, "not found")

                # 3) 目标变更
                rack_raw = body.rack_location if isinstance(body.rack_location, str) else None
                rack_up  = (rack_raw.strip().upper() if isinstance(rack_raw, str) and rack_raw.strip() != "" else None)
                final_qty  = (body.qty if body.qty is not None else old["qty"])
                final_rack = old["rack_location"] if rack_raw is None else (None if rack_raw == "" else rack_up)

                final_area = old["area"]
                if final_area in REQUIRED_RACK_AREAS and (final_rack is None or (isinstance(final_rack, str) and final_rack.strip() == "")):
                    raise HTTPException(400, f"rack_location required for area {final_area}")

                qty_changed  = (body.qty is not None and body.qty != old["qty"])
                rack_changed = (rack_raw is not None and final_rack != old["rack_location"])

                # 4) UPDATE
                cur.execute("""
                    UPDATE rfid_tags_current
                    SET qty=%s,
                        rack_location = CASE
                            WHEN %s IS NULL THEN rack_location
                            WHEN %s = '' THEN NULL
                            ELSE %s
                        END,
                        updated_at=NOW(), updated_by=%s
                    WHERE UPPER(tid)=UPPER(%s)
                """, (final_qty, rack_raw, rack_raw, rack_up, body.actor, tid))

                # 5) 读取新值
                cur.execute("""
                    SELECT tid, epc, label_number, muf_no, item, qty, batch_no, rack_location, area,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current WHERE UPPER(tid)=UPPER(%s)
                """, (tid,))
                new = _normalize_row(cur.fetchone())

                # 6) 日志
                action = "ADJUST_QTY" if qty_changed else ("MOVE" if rack_changed else "WRITE_INFO")
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
                    old["tid"], new["epc"], new["label_number"], new["item"], new["batch_no"],
                    action,
                    old["qty"], new["qty"],
                    old["rack_location"], new["rack_location"],
                    old["area"], new["area"],
                    body.remark, body.actor
                ))

                # 7) 审计
                result = "CHANGED" if (qty_changed or rack_changed) else "NO_CHANGES"
                notes_parts = []
                if qty_changed:
                    notes_parts.append(f"QTY: {old['qty']} -> {final_qty}")
                if rack_changed:
                    notes_parts.append(f"LOCATION: {old['rack_location'] or '-'} -> {final_rack or '-'}")
                change_notes = "; ".join(notes_parts) if notes_parts else None

                cur.execute("""
                    SELECT id FROM rfid_audit
                    WHERE UPPER(tid)=UPPER(%s)
                      AND qty_old <=> %s AND qty_new <=> %s
                      AND (from_rack_location <=> %s) AND (to_rack_location <=> %s)
                      AND (device_id <=> %s) AND (verified_by <=> %s)
                      AND created_at >= NOW() - INTERVAL 5 SECOND
                    ORDER BY id DESC LIMIT 1
                """, (old["tid"], old["qty"], final_qty, old["rack_location"], final_rack, body.device_id, body.actor))
                dup = cur.fetchone()
                if dup:
                    audit_id = dup["id"]
                else:
                    cur.execute("""
                        INSERT INTO rfid_audit
                        (tid, epc, label_number, item, batch_no, muf_no, area,
                         qty_old, qty_new, from_rack_location, to_rack_location,
                         result, change_notes, printed, remark, device_id, verified_by, created_at)
                        VALUES
                        (%s,%s,%s,%s,%s,%s,%s,
                         %s,%s,%s,%s,
                         %s,%s,0,%s,%s,%s,NOW())
                    """, (
                        old["tid"], new["epc"], new["label_number"], new["item"], new["batch_no"], old["muf_no"], old["area"],
                        old["qty"], final_qty, old["rack_location"], final_rack,
                        result, change_notes, body.remark, body.device_id, body.actor
                    ))
                    audit_id = cur.lastrowid

                conn.commit()
                return (audit_id, result, change_notes, new)
            except:
                conn.rollback(); raise
            finally:
                cur.close()
        finally:
            conn.close()

    audit_id, result, change_notes, latest = await asyncio.wait_for(asyncio.to_thread(_work), timeout=int(os.getenv("WRITE_TIMEOUT", "5")))
    return AuditResp(audit_id=audit_id, result=result, change_notes=change_notes, latest=latest)

class MarkPrintedReq(BaseModel):
    audit_id: int

@app.post("/audit/mark-printed", dependencies=[Depends(require_key)])
async def audit_mark_printed(body: MarkPrintedReq):
    pool = get_pool()
    def _work():
        conn = pool.get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            cur = conn.cursor()
            try:
                cur.execute("UPDATE rfid_audit SET printed=1, printed_at=NOW() WHERE id=%s", (body.audit_id,))
                return {"ok": True}
            finally:
                cur.close()
        finally:
            conn.close()
    return await asyncio.wait_for(asyncio.to_thread(_work), timeout=3)
