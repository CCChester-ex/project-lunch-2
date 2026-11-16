import os
import hmac
import hashlib
import secrets
import time
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Query, Body, Depends, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

try:
    from .database import get_db_connection, init_db
    from .models import (
        RestaurantBase, RestaurantCreate, RestaurantResponse,
        MenuItemCreate, MenuItemResponse,
        FeedbackCreate, FeedbackResponse,
        DayScheduleItem, WeeklyScheduleSaveRequest,
        OrderCreate, OrderResponse
    )
    from .scheduler import (
        calculate_restaurant_scores,
        validate_weekly_schedule,
        auto_generate_weekly_plan
    )
    from .time_window import (
        check_ordering_window,
        get_week_monday,
        get_active_week_monday,
        get_now_taipei,
        WEEKDAY_NAMES
    )
except ImportError:
    from database import get_db_connection, init_db
    from models import (
        RestaurantBase, RestaurantCreate, RestaurantResponse,
        MenuItemCreate, MenuItemResponse,
        FeedbackCreate, FeedbackResponse,
        DayScheduleItem, WeeklyScheduleSaveRequest,
        OrderCreate, OrderResponse
    )
    from scheduler import (
        calculate_restaurant_scores,
        validate_weekly_schedule,
        auto_generate_weekly_plan
    )
    from time_window import (
        check_ordering_window,
        get_week_monday,
        get_active_week_monday,
        get_now_taipei,
        WEEKDAY_NAMES
    )

app = FastAPI(title="高中庶務股長智慧訂餐系統", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")

# ==========================================
# 後台資安防護：HMAC 權杖簽章與防暴力破解
# ==========================================
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip() or secrets.token_hex(32)
TOKEN_TTL_SECONDS = 8 * 3600  # 8 小時過期

# 記錄各 IP 連續錯誤次數與鎖定到期時間: ip -> {"fails": int, "locked_until": float}
_LOGIN_ATTEMPTS: Dict[str, Dict[str, float]] = {}
MAX_LOGIN_FAILS = 5
LOCKOUT_SECONDS = 600  # 輸錯 5 次鎖定 10 分鐘


def get_admin_pin() -> str:
    """
    優先讀取環境變數 ADMIN_PIN，其次讀取資料庫 system_settings 裡的 admin_pin，
    若皆未設定才使用本機開發初始預設值。
    """
    env_pin = os.environ.get("ADMIN_PIN", "").strip()
    if env_pin:
        return env_pin
    db_pin = get_setting("admin_pin", "").strip()
    if db_pin:
        return db_pin
    return "66776677"


def create_admin_token() -> str:
    exp = int(time.time()) + TOKEN_TTL_SECONDS
    nonce = secrets.token_hex(8)
    payload = f"{exp}.{nonce}"
    sig = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_admin_token(token: str) -> bool:
    if not token or token.count(".") != 2:
        return False
    try:
        exp_str, nonce, sig = token.split(".")
        exp = int(exp_str)
        if time.time() > exp:
            return False
        payload = f"{exp_str}.{nonce}"
        expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False


def require_admin(authorization: Optional[str] = Header(None)):
    """FastAPI Dependency：強制保護所有 /api/admin/* 路由"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登入或權杖遺失，請重新登入庶務股長後台！")
    token = authorization[len("Bearer "):].strip()
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="後台登入時效已過期或無效，請重新輸入密碼登入！")
    return True


@app.on_event("startup")
def startup_event():
    init_db()
    ensure_current_week_schedule()

def get_setting(key: str, default: str = "") -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def ensure_week_schedule(monday_str: str):
    """確保指定週一開始的該週擁有完整的 5 天排餐，若未排則自動執行智慧排餐"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM weekly_schedules WHERE week_start_date = ?", (monday_str,))
    count = cursor.fetchone()[0]

    if count < 5:
        # 抓取前一週週五的店家類型，作為本週一避開重複類型的參考
        last_friday_cat = None
        try:
            prev_monday = (datetime.strptime(monday_str, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
            cursor.execute("""
                SELECT r.category FROM weekly_schedules ws
                JOIN restaurants r ON ws.restaurant_id = r.id
                WHERE ws.week_start_date = ? AND ws.day_of_week = 5
            """, (prev_monday,))
            r_cat = cursor.fetchone()
            if r_cat:
                last_friday_cat = r_cat["category"]
        except Exception:
            pass

        plan = auto_generate_weekly_plan(conn, last_friday_category=last_friday_cat)
        if plan:
            for day in plan:
                cursor.execute("""
                    INSERT OR REPLACE INTO weekly_schedules
                    (week_start_date, day_of_week, restaurant_id, is_manual_override, notes)
                    VALUES (?, ?, ?, ?, ?)
                """, (monday_str, day["day_of_week"], day["restaurant_id"], 0, day["notes"]))
            conn.commit()
    conn.close()

def ensure_current_week_schedule():
    """啟動時自動確保作用週與當前曆法週皆已排定（特別處理週日跨週問題）"""
    now = get_now_taipei()
    simulated_time = get_setting("simulated_time", "")
    if simulated_time:
        try:
            now = datetime.fromisoformat(simulated_time)
        except Exception:
            pass
    active_monday = get_active_week_monday(now)
    ensure_week_schedule(active_monday)
    cal_monday = get_week_monday(now)
    if cal_monday != active_monday:
        ensure_week_schedule(cal_monday)


# ==========================================
# 前台 / 點餐相關 API
# ==========================================

@app.get("/api/status")
def get_order_status():
    """取得當前訂餐時段狀態與今日/目標午餐餐廳（含紙本菜單圖連結與品項次分類）"""
    test_mode = get_setting("test_mode", "true") == "true"
    simulated_time = get_setting("simulated_time", "")

    now = get_now_taipei()
    if simulated_time:
        try:
            now = datetime.fromisoformat(simulated_time)
        except Exception:
            pass

    status = check_ordering_window(now, allow_test_override=test_mode)
    status["is_test_mode"] = test_mode

    target_date = status.get("target_meal_date")
    target_dow = status.get("target_day_of_week")

    restaurant_info = None
    if target_date and target_dow:
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        monday_str = get_week_monday(target_dt)
        ensure_week_schedule(monday_str)

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.*,
                   COALESCE(AVG(f.rating), 4.8) as avg_rating,
                   COUNT(f.id) as feedback_count
            FROM weekly_schedules ws
            JOIN restaurants r ON ws.restaurant_id = r.id
            LEFT JOIN feedbacks f ON r.id = f.restaurant_id
            WHERE ws.week_start_date = ? AND ws.day_of_week = ?
            GROUP BY r.id
        """, (monday_str, target_dow))
        row = cursor.fetchone()

        if row:
            rest_id = row["id"]
            cursor.execute("""
                SELECT * FROM menu_items
                WHERE restaurant_id = ? AND is_available = 1
                ORDER BY sub_category ASC, id ASC
            """, (rest_id,))
            menu_rows = cursor.fetchall()
            menu_items = [dict(m) for m in menu_rows]

            # 統計包含哪些次分類
            sub_cats = list(dict.fromkeys(m["sub_category"] for m in menu_items if m["sub_category"]))

            restaurant_info = {
                "id": row["id"],
                "name": row["name"],
                "category": row["category"],
                "phone": row["phone"],
                "min_order": row["min_order"],
                "notes": row["notes"],
                "image_url": row["image_url"] or "",
                "avg_rating": round(float(row["avg_rating"]), 1),
                "feedback_count": row["feedback_count"],
                "sub_categories": sub_cats,
                "menu_items": menu_items
            }
        conn.close()

    status["restaurant"] = restaurant_info
    return status


@app.get("/api/schedule/current-week")
def get_current_week_schedule(week_start: Optional[str] = None):
    """
    取得週一至週五的完整排餐行程。
    若未傳入 week_start，自動使用目前作用中的訂餐週（週日晚上自動切換為次週週一，如 11/09）。
    支援任意週次查詢（如 week_start=2026-11-09）。
    """
    simulated_time = get_setting("simulated_time", "")
    now = get_now_taipei()
    if simulated_time:
        try:
            now = datetime.fromisoformat(simulated_time)
        except Exception:
            pass

    if week_start:
        try:
            target_dt = datetime.strptime(week_start, "%Y-%m-%d")
            monday_dt = target_dt - timedelta(days=target_dt.weekday())
        except Exception:
            monday_dt = datetime.strptime(get_active_week_monday(now), "%Y-%m-%d")
    else:
        monday_dt = datetime.strptime(get_active_week_monday(now), "%Y-%m-%d")

    monday_str = monday_dt.strftime("%Y-%m-%d")
    ensure_week_schedule(monday_str)

    prev_week_monday = (monday_dt - timedelta(days=7)).strftime("%Y-%m-%d")
    next_week_monday = (monday_dt + timedelta(days=7)).strftime("%Y-%m-%d")
    active_monday = get_active_week_monday(now)

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ws.day_of_week, ws.is_manual_override, ws.notes as schedule_notes,
               r.id as restaurant_id, r.name as restaurant_name, r.category, r.phone, r.image_url,
               COALESCE(AVG(f.rating), 4.8) as avg_rating
        FROM weekly_schedules ws
        JOIN restaurants r ON ws.restaurant_id = r.id
        LEFT JOIN feedbacks f ON r.id = f.restaurant_id
        WHERE ws.week_start_date = ?
        GROUP BY ws.day_of_week, ws.is_manual_override, ws.notes, r.id
        ORDER BY ws.day_of_week ASC
    """, (monday_str,))
    rows = cursor.fetchall()

    result = []
    scheduled_map = {row["day_of_week"]: row for row in rows}

    for dow in range(1, 6):
        date_for_day = (monday_dt + timedelta(days=dow - 1)).strftime("%Y-%m-%d")
        day_label = WEEKDAY_NAMES[dow]
        if dow in scheduled_map:
            row = scheduled_map[dow]
            cursor.execute("SELECT * FROM menu_items WHERE restaurant_id = ? AND is_available = 1", (row["restaurant_id"],))
            items = [dict(m) for m in cursor.fetchall()]

            result.append({
                "day_of_week": dow,
                "day_name": day_label,
                "target_date": date_for_day,
                "restaurant_id": row["restaurant_id"],
                "restaurant_name": row["restaurant_name"],
                "category": row["category"],
                "phone": row["phone"],
                "image_url": row["image_url"] or "",
                "avg_rating": round(float(row["avg_rating"]), 1),
                "is_manual_override": row["is_manual_override"],
                "notes": row["schedule_notes"],
                "menu_items": items
            })
        else:
            result.append({
                "day_of_week": dow,
                "day_name": day_label,
                "target_date": date_for_day,
                "restaurant_id": None,
                "restaurant_name": "尚未排定",
                "category": "",
                "phone": "",
                "image_url": "",
                "avg_rating": 0.0,
                "is_manual_override": 0,
                "notes": "",
                "menu_items": []
            })

    conn.close()
    return {
        "week_start_date": monday_str,
        "prev_week_monday": prev_week_monday,
        "next_week_monday": next_week_monday,
        "is_active_week": (monday_str == active_monday),
        "days": result
    }


@app.post("/api/orders")
def place_order(order: OrderCreate):
    """
    同學送出點餐：支援單項或多品項購物車批次點餐（食量大可自選多項）
    """
    test_mode = get_setting("test_mode", "true") == "true"
    simulated_time = get_setting("simulated_time", "")
    now = get_now_taipei()
    if simulated_time:
        try:
            now = datetime.fromisoformat(simulated_time)
        except Exception:
            pass

    status = check_ordering_window(now, allow_test_override=test_mode)
    if not status["is_open"]:
        raise HTTPException(status_code=400, detail="目前非訂餐時段，無法送出訂單！" + status["message"])

    if not order.seat_number.strip():
        raise HTTPException(status_code=400, detail="請填寫座號！")
    if not order.student_name.strip():
        raise HTTPException(status_code=400, detail="請填寫姓名！")

    target_meal_date = status.get("target_meal_date") or order.order_date

    # 彙整要新增的項目
    items_to_insert = []
    if order.items and len(order.items) > 0:
        for it in order.items:
            if it.dish_name.strip():
                items_to_insert.append({
                    "dish_name": it.dish_name.strip(),
                    "price": it.price,
                    "quantity": it.quantity if it.quantity > 0 else 1,
                    "notes": it.notes.strip() if it.notes else ""
                })
    elif order.dish_name and order.dish_name.strip():
        items_to_insert.append({
            "dish_name": order.dish_name.strip(),
            "price": order.price if order.price is not None else 0,
            "quantity": order.quantity if order.quantity and order.quantity > 0 else 1,
            "notes": order.notes.strip() if order.notes else ""
        })

    if not items_to_insert:
        raise HTTPException(status_code=400, detail="點餐清單不可為空，請至少選擇或輸入一項餐點！")

    conn = get_db_connection()
    cursor = conn.cursor()

    order_ids = []
    total_price = 0
    total_qty = 0

    for item in items_to_insert:
        cursor.execute("""
            INSERT INTO orders (order_date, seat_number, student_name, dish_name, price, quantity, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            target_meal_date,
            order.seat_number.strip(),
            order.student_name.strip(),
            item["dish_name"],
            item["price"],
            item["quantity"],
            item["notes"]
        ))
        order_ids.append(cursor.lastrowid)
        total_price += item["price"] * item["quantity"]
        total_qty += item["quantity"]

    conn.commit()
    conn.close()

    summary_names = "、".join(i["dish_name"] for i in items_to_insert[:3])
    if len(items_to_insert) > 3:
        summary_names += f" 等共 {len(items_to_insert)} 項"

    return {
        "success": True,
        "message": f"點餐成功！座號 {order.seat_number} 號 {order.student_name}：【{summary_names}】共 {total_qty} 份，合計 {total_price} 元",
        "order_ids": order_ids,
        "total_price": total_price,
        "total_qty": total_qty
    }


@app.get("/api/orders/my-history")
def get_student_order_history(
    seat_number: str = Query(..., description="同學座號"),
    date: Optional[str] = Query(None, description="查詢指定日期 YYYY-MM-DD，若未傳則查詢今日或最近點餐")
):
    """同學自助查詢個人點餐記錄 (今日或最近紀錄)"""
    seat = seat_number.strip()
    if not seat:
        raise HTTPException(status_code=400, detail="請輸入座號")

    conn = get_db_connection()
    cursor = conn.cursor()

    if date:
        cursor.execute("""
            SELECT * FROM orders
            WHERE seat_number = ? AND order_date = ?
            ORDER BY id ASC
        """, (seat, date))
    else:
        # 先查目標用餐日或今日
        test_mode = get_setting("test_mode", "true") == "true"
        simulated_time = get_setting("simulated_time", "")
        now = get_now_taipei()
        if simulated_time:
            try:
                now = datetime.fromisoformat(simulated_time)
            except Exception:
                pass
        status = check_ordering_window(now, allow_test_override=test_mode)
        target_date = status.get("target_meal_date") or now.strftime("%Y-%m-%d")

        cursor.execute("""
            SELECT * FROM orders
            WHERE seat_number = ? AND order_date = ?
            ORDER BY id ASC
        """, (seat, target_date))
        rows = cursor.fetchall()
        if not rows:
            # 若今日尚未點餐，回傳最近一次的歷史紀錄方便同學核對
            cursor.execute("""
                SELECT order_date FROM orders
                WHERE seat_number = ?
                ORDER BY order_date DESC LIMIT 1
            """, (seat,))
            latest = cursor.fetchone()
            if latest:
                target_date = latest["order_date"]
                cursor.execute("""
                    SELECT * FROM orders
                    WHERE seat_number = ? AND order_date = ?
                    ORDER BY id ASC
                """, (seat, target_date))

    rows = cursor.fetchall() if date else rows
    orders = [dict(r) for r in rows]

    total_amount = sum(o["price"] * o["quantity"] for o in orders)
    total_qty = sum(o["quantity"] for o in orders)
    is_all_paid = all(o["is_paid"] == 1 for o in orders) if orders else False
    student_name = orders[0]["student_name"] if orders else ""
    order_date = orders[0]["order_date"] if orders else date

    conn.close()

    return {
        "found": len(orders) > 0,
        "seat_number": seat,
        "student_name": student_name,
        "order_date": order_date,
        "total_amount": total_amount,
        "total_qty": total_qty,
        "is_paid": is_all_paid,
        "items": orders
    }


@app.get("/api/orders/today")
def get_today_orders(date: Optional[str] = None):
    """
    取得當日點餐統計：
    1. 各菜色數量彙總 (電話叫餐用)
    2. 同學個人清單合併視圖 (依座號姓名分組，方便收費核對)
    3. 完整原始明細清單
    """
    if not date:
        test_mode = get_setting("test_mode", "true") == "true"
        simulated_time = get_setting("simulated_time", "")
        now = get_now_taipei()
        if simulated_time:
            try:
                now = datetime.fromisoformat(simulated_time)
            except Exception:
                pass
        status = check_ordering_window(now, allow_test_override=test_mode)
        date = status.get("target_meal_date") or now.strftime("%Y-%m-%d")

    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. 訂單原始清單
    cursor.execute("""
        SELECT * FROM orders
        WHERE order_date = ?
        ORDER BY id ASC
    """, (date,))
    all_orders = [dict(row) for row in cursor.fetchall()]
    all_orders.sort(key=lambda o: (int(o["seat_number"]) if str(o["seat_number"]).isdigit() else 999, o["id"]))

    # 2. 彙整各菜色總數量 (方便電話叫餐)
    cursor.execute("""
        SELECT dish_name, price, SUM(quantity) as total_qty, SUM(price * quantity) as subtotal
        FROM orders
        WHERE order_date = ?
        GROUP BY dish_name, price
        ORDER BY total_qty DESC
    """, (date,))
    dish_summary = [dict(row) for row in cursor.fetchall()]

    # 3. 依同學 (座號 + 姓名) 分組彙總
    students_map = {}
    for o in all_orders:
        key = (o["seat_number"], o["student_name"])
        if key not in students_map:
            students_map[key] = {
                "seat_number": o["seat_number"],
                "student_name": o["student_name"],
                "total_price": 0,
                "total_qty": 0,
                "is_paid": 1,
                "order_ids": [],
                "items": []
            }
        students_map[key]["total_price"] += o["price"] * o["quantity"]
        students_map[key]["total_qty"] += o["quantity"]
        students_map[key]["order_ids"].append(o["id"])
        if o["is_paid"] == 0:
            students_map[key]["is_paid"] = 0
        students_map[key]["items"].append(o)

    student_orders = list(students_map.values())
    student_orders.sort(key=lambda s: int(s["seat_number"]) if s["seat_number"].isdigit() else 999)

    total_qty = sum(item["total_qty"] for item in dish_summary)
    total_amount = sum(item["subtotal"] for item in dish_summary)
    paid_students_count = sum(1 for s in student_orders if s["is_paid"] == 1)

    conn.close()

    return {
        "order_date": date,
        "total_qty": total_qty,
        "total_amount": total_amount,
        "total_students": len(student_orders),
        "paid_count": paid_students_count,
        "dish_summary": dish_summary,
        "student_orders": student_orders,
        "orders": all_orders
    }


@app.patch("/api/admin/orders/student-payment")
def toggle_student_payment(payload: Dict[str, Any] = Body(...), _: bool = Depends(require_admin)):
    """一鍵切換該同學所有項目的繳費狀態"""
    seat_number = payload.get("seat_number")
    date = payload.get("order_date")
    is_paid = 1 if payload.get("is_paid") else 0

    if not seat_number or not date:
        raise HTTPException(status_code=400, detail="請提供座號與日期")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE orders
        SET is_paid = ?
        WHERE order_date = ? AND seat_number = ?
    """, (is_paid, date, seat_number))
    conn.commit()
    conn.close()

    return {"success": True, "is_paid": is_paid}


@app.post("/api/feedbacks")
def submit_feedback(fb: FeedbackCreate):
    """同學提交餐廳回饋與評分"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO feedbacks (restaurant_id, seat_number, student_name, rating, comment, tags)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (fb.restaurant_id, fb.seat_number, fb.student_name, fb.rating, fb.comment, fb.tags))
    conn.commit()
    conn.close()
    return {"success": True, "message": "感謝您的回饋！這將作為下週排餐演算法的重要參考依據。"}


# ==========================================
# 庶務股長後台管理 API (全數受 require_admin 保護)
# ==========================================

@app.post("/api/admin/schedule/auto-plan")
def auto_plan_schedule(payload: Optional[Dict[str, Any]] = Body(None), _: bool = Depends(require_admin)):
    """一鍵智慧自動排餐，支援指定排餐週並參考前週五類型避免重複"""
    week_start_date = payload.get("week_start_date") if payload else None

    conn = get_db_connection()
    last_friday_cat = None
    if week_start_date:
        try:
            prev_monday = (datetime.strptime(week_start_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.category FROM weekly_schedules ws
                JOIN restaurants r ON ws.restaurant_id = r.id
                WHERE ws.week_start_date = ? AND ws.day_of_week = 5
            """, (prev_monday,))
            r_cat = cursor.fetchone()
            if r_cat:
                last_friday_cat = r_cat["category"]
        except Exception:
            pass

    plan = auto_generate_weekly_plan(conn, last_friday_category=last_friday_cat)
    conn.close()

    if not plan:
        raise HTTPException(
            status_code=400,
            detail="無法自動產出排餐！請確保系統內至少有 5 家不同類別之啟用餐廳。"
        )

    return {"success": True, "plan": plan}


@app.post("/api/admin/schedule/validate")
def validate_schedule_endpoint(payload: Dict[str, Any] = Body(...), _: bool = Depends(require_admin)):
    """檢驗排程約束（含每週同店不重複、同類型不連續、店家公休日避開）"""
    days = payload.get("days", [])

    # 查出餐廳公休日資訊
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, name, category, COALESCE(closed_days, '') as closed_days FROM restaurants")
    meta = {r["id"]: dict(r) for r in c.fetchall()}
    conn.close()

    result = validate_weekly_schedule(days, restaurant_meta=meta)
    return result


@app.post("/api/admin/schedule/save")
def save_weekly_schedule(req: WeeklyScheduleSaveRequest, _: bool = Depends(require_admin)):
    """庶務股長儲存每週排餐設定"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, name, category, COALESCE(closed_days, '') as closed_days FROM restaurants")
    meta = {r["id"]: dict(r) for r in c.fetchall()}

    validation = validate_weekly_schedule([d.dict() for d in req.days], restaurant_meta=meta)
    if not validation["valid"]:
        conn.close()
        raise HTTPException(status_code=400, detail="; ".join(validation["errors"]))

    cursor = conn.cursor()

    for day in req.days:
        cursor.execute("""
            INSERT OR REPLACE INTO weekly_schedules
            (week_start_date, day_of_week, restaurant_id, is_manual_override, notes)
            VALUES (?, ?, ?, ?, ?)
        """, (
            req.week_start_date,
            day.day_of_week,
            day.restaurant_id,
            1,
            day.notes or "股長排定"
        ))

    conn.commit()
    conn.close()
    return {"success": True, "message": "本週排餐計畫已成功儲存！"}


@app.get("/api/admin/restaurants")
def get_all_restaurants(_: bool = Depends(require_admin)):
    """取得所有餐廳名單與菜單、平均評分、實體菜單圖"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.*,
               COALESCE(AVG(f.rating), 4.8) as avg_rating,
               COUNT(f.id) as feedback_count
        FROM restaurants r
        LEFT JOIN feedbacks f ON r.id = f.restaurant_id
        GROUP BY r.id
        ORDER BY r.id ASC
    """)
    rows = cursor.fetchall()

    restaurants = []
    for r in rows:
        cursor.execute("SELECT * FROM menu_items WHERE restaurant_id = ? ORDER BY sub_category, id", (r["id"],))
        items = [dict(m) for m in cursor.fetchall()]
        sub_cats = list(dict.fromkeys(m["sub_category"] for m in items if m["sub_category"]))
        restaurants.append({
            "id": r["id"],
            "name": r["name"],
            "category": r["category"],
            "phone": r["phone"],
            "min_order": r["min_order"],
            "notes": r["notes"],
            "image_url": r["image_url"] or "",
            "closed_days": r["closed_days"] if "closed_days" in r.keys() and r["closed_days"] else "",
            "is_active": r["is_active"],
            "avg_rating": round(float(r["avg_rating"]), 1),
            "feedback_count": r["feedback_count"],
            "sub_categories": sub_cats,
            "menu_items": items
        })

    conn.close()
    return restaurants


@app.post("/api/admin/restaurants")
def create_restaurant(rest: RestaurantCreate, _: bool = Depends(require_admin)):
    """新增餐廳與其菜單"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO restaurants (name, category, phone, min_order, notes, image_url, closed_days)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (rest.name.strip(), rest.category.strip(), rest.phone.strip(), rest.min_order, rest.notes.strip(), rest.image_url.strip() if rest.image_url else "", rest.closed_days.strip() if rest.closed_days else ""))
        rest_id = cursor.lastrowid

        for m in rest.menu_items:
            cursor.execute("""
                INSERT INTO menu_items (restaurant_id, sub_category, name, price, description)
                VALUES (?, ?, ?, ?, ?)
            """, (rest_id, m.sub_category or "主要餐點", m.name.strip(), m.price, m.description.strip() if m.description else ""))

        conn.commit()
        conn.close()
        return {"success": True, "restaurant_id": rest_id, "message": f"成功新增餐廳【{rest.name}】！"}
    except Exception:
        conn.close()
        raise HTTPException(status_code=400, detail=f"已存在同名餐廳【{rest.name}】！")


@app.put("/api/admin/restaurants/{rest_id}")
def update_restaurant(rest_id: int, rest: RestaurantBase, _: bool = Depends(require_admin)):
    """更新餐廳基本資料（含公休日設定、電話、菜單備註、實體菜單圖等）"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE restaurants
        SET name = ?, category = ?, phone = ?, min_order = ?, notes = ?, image_url = ?, closed_days = ?, is_active = ?
        WHERE id = ?
    """, (
        rest.name.strip(),
        rest.category.strip(),
        rest.phone.strip(),
        rest.min_order,
        rest.notes.strip(),
        rest.image_url.strip() if rest.image_url else "",
        rest.closed_days.strip() if rest.closed_days else "",
        rest.is_active,
        rest_id
    ))
    conn.commit()
    conn.close()
    return {"success": True, "message": f"成功更新餐廳【{rest.name}】資料！"}


@app.delete("/api/admin/restaurants/{rest_id}")
def delete_restaurant(rest_id: int, _: bool = Depends(require_admin)):
    """刪除餐廳"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM restaurants WHERE id = ?", (rest_id,))
    conn.commit()
    conn.close()
    return {"success": True, "message": "餐廳已刪除！"}


@app.post("/api/admin/restaurants/{rest_id}/menu")
def add_menu_item(rest_id: int, item: MenuItemCreate, _: bool = Depends(require_admin)):
    """為餐廳追加菜品"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO menu_items (restaurant_id, sub_category, name, variant, price, description)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (rest_id, item.sub_category or "主要餐點", item.name.strip(), (item.variant or "").strip(), item.price, item.description.strip() if item.description else ""))
    item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"success": True, "item_id": item_id, "message": f"已新增菜色【{item.name}】"}


@app.delete("/api/admin/menu/{item_id}")
def delete_menu_item(item_id: int, _: bool = Depends(require_admin)):
    """刪除菜單項目"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM menu_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return {"success": True, "message": "菜色已刪除"}


@app.patch("/api/admin/orders/{order_id}/payment")
def toggle_payment(order_id: int, payload: Dict[str, Any] = Body(...), _: bool = Depends(require_admin)):
    """切換單筆訂單收費狀態"""
    is_paid = 1 if payload.get("is_paid") else 0
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET is_paid = ? WHERE id = ?", (is_paid, order_id))
    conn.commit()
    conn.close()
    return {"success": True, "is_paid": is_paid}


@app.post("/api/admin/orders/batch-payment")
def batch_payment(payload: Dict[str, Any] = Body(...), _: bool = Depends(require_admin)):
    """批次將指定日期的訂單全部設為已收費或未收費"""
    date = payload.get("date")
    is_paid = 1 if payload.get("is_paid") else 0
    if not date:
        raise HTTPException(status_code=400, detail="請指定日期")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET is_paid = ? WHERE order_date = ?", (is_paid, date))
    conn.commit()
    conn.close()
    return {"success": True, "message": f"已將 {date} 之所有訂單收費狀態更新為 {'已收費' if is_paid else '未收費'}"}


@app.delete("/api/admin/orders/{order_id}")
def delete_order(order_id: int, _: bool = Depends(require_admin)):
    """刪除訂單"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    return {"success": True, "message": "訂單已取消/刪除"}


@app.get("/api/admin/feedbacks")
def get_feedbacks(_: bool = Depends(require_admin)):
    """查詢同學評價與回饋清單"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT f.*, r.name as restaurant_name
        FROM feedbacks f
        JOIN restaurants r ON f.restaurant_id = r.id
        ORDER BY f.created_at DESC
    """)
    feedbacks = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return feedbacks


@app.post("/api/admin/verify-pin")
def verify_admin_pin(request: Request, payload: Dict[str, Any] = Body(...)):
    """驗證庶務股長後台密碼（含防暴力破解與 HMAC 簽章權杖核發）"""
    client_ip = request.client.host if request.client else "unknown"
    now_ts = time.time()

    attempt_info = _LOGIN_ATTEMPTS.get(client_ip, {"fails": 0, "locked_until": 0.0})
    if attempt_info["locked_until"] > now_ts:
        remain_min = max(1, int((attempt_info["locked_until"] - now_ts + 59) // 60))
        raise HTTPException(
            status_code=429,
            detail=f"密碼連續輸入錯誤次數過多，系統已暫時鎖定！請於 {remain_min} 分鐘後再試。"
        )

    pin = str(payload.get("pin", "")).strip()
    expected_pin = get_admin_pin()

    if pin and hmac.compare_digest(pin.encode("utf-8"), expected_pin.encode("utf-8")):
        _LOGIN_ATTEMPTS.pop(client_ip, None)
        token = create_admin_token()
        return {
            "success": True,
            "token": token,
            "expires_in": TOKEN_TTL_SECONDS,
            "message": "密碼驗證成功，歡迎進入後台"
        }

    fails = int(attempt_info.get("fails", 0)) + 1
    if fails >= MAX_LOGIN_FAILS:
        _LOGIN_ATTEMPTS[client_ip] = {"fails": fails, "locked_until": now_ts + LOCKOUT_SECONDS}
        raise HTTPException(
            status_code=429,
            detail="密碼連續錯誤達 5 次，已鎖定 10 分鐘！"
        )
    else:
        _LOGIN_ATTEMPTS[client_ip] = {"fails": fails, "locked_until": 0.0}
        left = MAX_LOGIN_FAILS - fails
        raise HTTPException(
            status_code=401,
            detail=f"密碼錯誤！您還有 {left} 次嘗試機會。"
        )


@app.get("/api/admin/settings")
def get_settings(_: bool = Depends(require_admin)):
    """取得系統環境設定與 Discord Webhook 配置"""
    test_mode = get_setting("test_mode", "true") == "true"
    simulated_time = get_setting("simulated_time", "")
    discord_webhook_url = get_setting("discord_webhook_url", "")
    discord_enabled = get_setting("discord_enabled", "false") == "true"
    return {
        "test_mode": test_mode,
        "simulated_time": simulated_time,
        "discord_webhook_url": discord_webhook_url,
        "discord_enabled": discord_enabled,
        "current_server_time": get_now_taipei().strftime("%Y-%m-%d %H:%M:%S")
    }


@app.post("/api/admin/settings")
def update_settings(payload: Dict[str, Any] = Body(...), _: bool = Depends(require_admin)):
    """更新測試模式、模擬時間、Discord Webhook 或管理員密碼"""
    if "test_mode" in payload:
        set_setting("test_mode", "true" if payload["test_mode"] else "false")
    if "simulated_time" in payload:
        set_setting("simulated_time", payload["simulated_time"])
    if "discord_webhook_url" in payload:
        set_setting("discord_webhook_url", payload["discord_webhook_url"].strip())
    if "discord_enabled" in payload:
        set_setting("discord_enabled", "true" if payload["discord_enabled"] else "false")
    if "new_admin_pin" in payload and str(payload["new_admin_pin"]).strip():
        new_pin = str(payload["new_admin_pin"]).strip()
        if len(new_pin) < 6:
            raise HTTPException(status_code=400, detail="新密碼長度請至少設定 6 碼以上！")
        set_setting("admin_pin", new_pin)
    return {"success": True, "message": "設定已更新"}


@app.post("/api/admin/discord/test")
def test_discord_webhook(payload: Dict[str, Any] = Body(...), _: bool = Depends(require_admin)):
    """測試發送 Discord 班群點餐提醒 Webhook"""
    import urllib.request
    import json

    webhook_url = payload.get("webhook_url", "").strip() or get_setting("discord_webhook_url", "")
    if not webhook_url:
        raise HTTPException(status_code=400, detail="請先填寫 Discord Webhook 網址！")

    # 取得當前推薦店家資訊
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT name, category, phone, notes FROM restaurants LIMIT 1")
    r = c.fetchone()
    conn.close()

    rest_name = r["name"] if r else "成大館 外帶單"
    rest_cat = r["category"] if r else "便當快炒"

    discord_data = {
        "content": "📢 **【高三2班 午餐訂餐提醒】** 同學們好！今日訂餐時段已開放囉～",
        "embeds": [
            {
                "title": f"🍱 今日主打推薦店家：{rest_name}（{rest_cat}）",
                "description": "⏰ **訂餐時段**：週日到週四 16:00 ～ 隔天 10:00 截止\n👉 請大家記得在截止時間前點進系統完成點餐！",
                "color": 16347414,  # Orange
                "fields": [
                    {"name": "🏬 合作店家", "value": rest_name, "inline": True},
                    {"name": "🍲 料理風格", "value": rest_cat, "inline": True},
                    {"name": "🔗 線上點餐系統", "value": "[點擊立即前往點餐](http://localhost:8000)", "inline": False}
                ],
                "footer": {
                    "text": "高中庶務股長智慧訂餐系統 • 班群自動推播機器人"
                },
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        ]
    }

    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(discord_data).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "SchoolLunchBot/1.0"}
        )
        with urllib.request.urlopen(req, timeout=6) as response:
            if response.status in (200, 204):
                return {"success": True, "message": "✓ Discord 班群測試訊息發送成功！請檢查 Discord 頻道。"}
            return {"success": False, "message": f"發送回應狀態碼：{response.status}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Discord 發送失敗: {str(e)}")


# ==========================================
# 靜態首頁與後台路由
# ==========================================

@app.get("/")
def serve_home():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

@app.get("/admin")
def serve_admin():
    return FileResponse(os.path.join(FRONTEND_DIR, "admin.html"))

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
