# main.py  (对齐 rfid_tags_current / rfid_tags_log)
import os, asyncio
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import pooling

load_dotenv()

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
    qty: int | None = None
    rack_location: str | None = None
    stock_status: str | None = None
    remark: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None

class UpdateReq(BaseModel):
    qty: int | None = None
    rack_location: str | None = None
    epc: str | None = None          # ← 新增：允许客户端顺带把新 EPC 提交上来
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
    rack_location: str | None = None
    stock_status: str = "IN_STOCK"
    remark: str | None = None
    actor: str = "userA"
    device_id: str = "T1U-1"

app = FastAPI()

@app.get("/health")
async def health():
    return {"ok": True}

# ---------- Query ----------
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
                    SELECT tid, epc, label_number, item, qty, rack_location, stock_status,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current WHERE epc=%s LIMIT 1
                """, (epc,))
                row = cur.fetchone()
                return row
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
                    SELECT tid, epc, label_number, item, qty, rack_location, stock_status,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current WHERE tid=%s LIMIT 1
                """, (tid,))
                row = cur.fetchone()
                return row
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
    pool = get_pool()
    def _work():
        conn = pool.get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            cur = conn.cursor(dictionary=True)
            try:
                # 1) 读当前行并加锁
                cur.execute("""
                    SELECT tid, epc, label_number, item, qty, rack_location, stock_status
                    FROM rfid_tags_current WHERE tid=%s FOR UPDATE
                """, (tid,))
                old = cur.fetchone()
                if not old:
                    raise mysql.connector.Error("not found")

                # 2) 同步更新 current（如果 body.epc 为空，就保持原来的 epc）
                cur.execute("""
                    UPDATE rfid_tags_current
                    SET epc=COALESCE(%s, epc),
                        qty=COALESCE(%s, qty),
                        rack_location=COALESCE(%s, rack_location),
                        updated_at=NOW(), updated_by=%s
                    WHERE tid=%s
                """, (body.epc, body.qty, body.rack_location, body.actor, tid))

                epc_new = body.epc or old["epc"]

                # 3) 记录日志（使用你传进来的 action）
                cur.execute("""
                    INSERT INTO rfid_tags_log
                    (tid, epc, label_number, item, action,
                     qty_old, qty_new, from_rack_location, to_rack_location,
                     stock_status_old, stock_status_new, remark,
                     updated_at, updated_by)
                    VALUES
                    (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s)
                """, (
                    tid, epc_new, old["label_number"], old["item"], body.action,
                    old["qty"], body.qty,
                    old["rack_location"], body.rack_location,
                    old["stock_status"], old["stock_status"],
                    body.remark, body.actor
                ))

                # 4) 返回最新行
                cur.execute("""
                    SELECT tid, epc, label_number, item, qty, rack_location, stock_status,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current WHERE tid=%s
                """, (tid,))
                return cur.fetchone()
            finally:
                cur.close()
        finally:
            conn.close()

    row = await asyncio.wait_for(asyncio.to_thread(_work), timeout=int(os.getenv("WRITE_TIMEOUT", "4")))
    if not row:
        raise HTTPException(404, "not found")
    return row

# ---------- Register ----------
@app.post("/tags/register", dependencies=[Depends(require_key)])
async def register(body: RegisterReq):
    pool = get_pool()
    def _work():
        conn = pool.get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            conn.start_transaction()
            cur = conn.cursor(dictionary=True)
            try:
                # Upsert current
                cur.execute("""
                    INSERT INTO rfid_tags_current
                    (tid, label_number, epc, item, qty, rack_location, stock_status, remark, updated_at, updated_by)
                    VALUES
                    (%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s)
                    ON DUPLICATE KEY UPDATE
                    label_number=VALUES(label_number),
                    epc=VALUES(epc),
                    item=VALUES(item),
                    qty=VALUES(qty),
                    rack_location=VALUES(rack_location),
                    stock_status=VALUES(stock_status),
                    remark=VALUES(remark),
                    updated_at=NOW(),
                    updated_by=VALUES(updated_by)
                """, (
                    body.tid, body.label_number, body.epc, body.item, body.qty,
                    body.rack_location, body.stock_status, body.remark, body.actor
                ))

                # Log
                cur.execute("""
                    INSERT INTO rfid_tags_log
                    (tid, epc, label_number, item, action,
                     qty_old, qty_new, from_rack_location, to_rack_location,
                     stock_status_old, stock_status_new, remark,
                     updated_at, updated_by)
                    VALUES
                    (%s,%s,%s,%s,'REGISTER',NULL,%s,NULL,%s,NULL,%s,%s,NOW(),%s)
                """, (
                    body.tid, body.epc, body.label_number, body.item,
                    body.qty, body.rack_location, body.stock_status,
                    body.remark, body.actor
                ))
                conn.commit()

                cur.execute("""
                    SELECT tid, epc, label_number, item, qty, rack_location, stock_status,
                           remark, updated_at, updated_by
                    FROM rfid_tags_current WHERE tid=%s LIMIT 1
                """, (body.tid,))
                return cur.fetchone()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
        finally:
            conn.close()
    row = await asyncio.wait_for(asyncio.to_thread(_work), timeout=int(os.getenv("WRITE_TIMEOUT", "4")))
    if not row:
        raise HTTPException(500, "save failed")
    return row


# # main.py
# import os, asyncio
# from fastapi import FastAPI, HTTPException, Depends, Header
# from pydantic import BaseModel
# from dotenv import load_dotenv
# import mysql.connector
# from mysql.connector import pooling

# load_dotenv()

# _POOL = None
# def get_pool():
#     global _POOL
#     if _POOL is None:
#         _POOL = pooling.MySQLConnectionPool(
#             pool_name="rfid_pool",
#             pool_size=10,
#             host=os.getenv("DB_HOST"),
#             port=int(os.getenv("DB_PORT", "3306")),
#             database=os.getenv("DB_NAME"),
#             user=os.getenv("DB_USER"),
#             password=os.getenv("DB_PASS"),
#             autocommit=True,                 # 读操作无需显式事务
#             connection_timeout=int(os.getenv("CONNECT_TIMEOUT", "3")),
#             pool_reset_session=True
#         )
#     return _POOL

# API_KEY = os.getenv("API_KEY", "changeme")

# async def require_key(x_api_key: str = Header(None)):
#     if x_api_key != API_KEY:
#         raise HTTPException(401, "Unauthorized")

# class InventoryRow(BaseModel):
#     epc: str
#     item_code: str | None = None
#     battery_type: str | None = None
#     quantity: float | None = None
#     location_code: str | None = None
#     stock_status: str | None = None
#     updated_at: str | None = None
#     updated_by: str | None = None

# class UpdateReq(BaseModel):
#     quantity: float | None = None
#     location_code: str | None = None
#     actor: str | None = None
#     device_id: str | None = None

# app = FastAPI()

# @app.get("/health")
# async def health():
#     return {"ok": True}

# @app.get("/inventory/by-epc", dependencies=[Depends(require_key)])
# async def find_by_epc(epc: str):
#     pool = get_pool()

#     # 注意：这里必须是同步函数（def），不能 async def
#     def _work():
#         conn = pool.get_connection()
#         try:
#             # 关键：拿到连接后先 ping，必要时自动重连
#             conn.ping(reconnect=True, attempts=1, delay=0)
#             # 去掉 buffered=True，减少额外拷贝/等待
#             cur = conn.cursor(dictionary=True)
#             try:
#                 cur.execute("""
#                     SELECT epc, Item_code AS item_code, battery_type, qty AS quantity,
#                            location_code, stock_status, updated_at, updated_by
#                     FROM rfid_tags_current WHERE epc=%s LIMIT 1
#                 """, (epc,))
#                 row = cur.fetchone()
#                 if not row:
#                     return None
#                 return row
#             finally:
#                 cur.close()
#         finally:
#             conn.close()

#     try:
#         row = await asyncio.wait_for(
#             asyncio.to_thread(_work),
#             timeout=int(os.getenv("READ_TIMEOUT", "3"))
#         )
#         if row is None:
#             raise HTTPException(404, "not found")
#         return row
#     except asyncio.TimeoutError:
#         raise HTTPException(504, "db read timeout")

# @app.patch("/inventory/{epc}", dependencies=[Depends(require_key)])
# async def update(epc: str, body: UpdateReq):
#     pool = get_pool()

#     def _work():
#         conn = pool.get_connection()
#         try:
#             conn.ping(reconnect=True, attempts=1, delay=0)
#             conn.start_transaction()
#             cur = conn.cursor(dictionary=True)
#             try:
#                 cur.execute("""
#                     SELECT Item_code AS item_code, battery_type, qty, location_code
#                     FROM rfid_tags_current WHERE epc=%s FOR UPDATE
#                 """, (epc,))
#                 old = cur.fetchone()
#                 if not old:
#                     conn.rollback()
#                     return None

#                 cur.execute("""
#                     UPDATE rfid_tags_current
#                     SET qty=COALESCE(%s, qty),
#                         location_code=COALESCE(%s, location_code),
#                         updated_at=NOW(), updated_by=%s
#                     WHERE epc=%s
#                 """, (body.quantity, body.location_code, body.actor or "", epc))

#                 cur.execute("""
#                     INSERT INTO rfid_tags_log
#                     (epc, action, item_code, battery_type, qty_old, qty_new,
#                      from_location, to_location, updated_at, updated_by, remark)
#                     VALUES (%s,'WRITE_INFO',%s,%s,%s,%s,%s,%s,NOW(),%s,%s)
#                 """, (epc, old["item_code"], old["battery_type"],
#                       old["qty"], body.quantity,
#                       old["location_code"], body.location_code,
#                       body.actor or "", f"by DPA {body.device_id or ''}"))
#                 conn.commit()

#                 # 读回最新
#                 cur.execute("""
#                     SELECT epc, Item_code AS item_code, battery_type, qty AS quantity,
#                            location_code, stock_status, updated_at, updated_by
#                     FROM rfid_tags_current WHERE epc=%s LIMIT 1
#                 """, (epc,))
#                 return cur.fetchone()
#             except Exception:
#                 conn.rollback()
#                 raise
#             finally:
#                 cur.close()
#         finally:
#             conn.close()

#     try:
#         row = await asyncio.wait_for(
#             asyncio.to_thread(_work),
#             timeout=int(os.getenv("READ_TIMEOUT", "3"))
#         )
#         if row is None:
#             raise HTTPException(404, "not found")
#         return row
#     except asyncio.TimeoutError:
#         raise HTTPException(504, "db write timeout")
