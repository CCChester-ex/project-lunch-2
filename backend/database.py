import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

# 嘗試讀取專案根目錄下的 .env 檔案（供本機開發設定環境變數）
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
ENV_FILE = os.path.join(ROOT_DIR, ".env")
if os.path.exists(ENV_FILE):
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

DB_DIR = os.path.join(ROOT_DIR, "data")
DB_PATH = os.path.join(DB_DIR, "lunch.db")


def is_postgres_mode() -> bool:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url.startswith("postgres://") or url.startswith("postgresql://")


class HybridRow:
    """同時支援 row['col'] 與 row[0] 與 dict(row) 的跨資料庫資料列"""
    def __init__(self, columns, values):
        self._cols = list(columns)
        self._vals = tuple(values)
        self._map = dict(zip(self._cols, self._vals))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return self._map[key]

    def get(self, key, default=None):
        return self._map.get(key, default)

    def keys(self):
        return self._map.keys()

    def items(self):
        return self._map.items()

    def values(self):
        return self._map.values()

    def __iter__(self):
        return iter(self._cols)

    def __contains__(self, item):
        return item in self._map

    def __len__(self):
        return len(self._vals)


class PostgresCursorWrapper:
    """將 SQLite 風格的 '?' 佔位符與 cursor.lastrowid 自動轉換為 PostgreSQL 相容語法"""
    def __init__(self, raw_cursor):
        self._cur = raw_cursor
        self.lastrowid: Optional[int] = None

    def _translate_sql(self, sql: str) -> str:
        s = sql.strip()
        # 轉換 INSERT OR REPLACE
        if re.match(r"^INSERT\s+OR\s+REPLACE\s+INTO\s+system_settings", s, flags=re.IGNORECASE):
            s = re.sub(
                r"^INSERT\s+OR\s+REPLACE\s+INTO\s+system_settings\s*\(\s*key\s*,\s*value\s*\)\s*VALUES\s*\(\s*\?\s*,\s*\?\s*\)",
                "INSERT INTO system_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                s,
                flags=re.IGNORECASE
            )
        elif re.match(r"^INSERT\s+OR\s+REPLACE\s+INTO\s+weekly_schedules", s, flags=re.IGNORECASE):
            s = re.sub(
                r"^INSERT\s+OR\s+REPLACE\s+INTO\s+weekly_schedules",
                "INSERT INTO weekly_schedules",
                s,
                flags=re.IGNORECASE
            )
            if "ON CONFLICT" not in s.upper():
                s += (
                    " ON CONFLICT (week_start_date, day_of_week) DO UPDATE SET "
                    "restaurant_id = EXCLUDED.restaurant_id, "
                    "is_manual_override = EXCLUDED.is_manual_override, "
                    "notes = EXCLUDED.notes"
                )
        # 將 ? 轉為 %s
        s = s.replace("?", "%s")
        return s

    def execute(self, sql: str, params: Any = None):
        pg_sql = self._translate_sql(sql)
        is_insert_with_id = (
            re.match(r"^INSERT\s+INTO\s+(restaurants|menu_items|feedbacks|weekly_schedules|orders)\b", pg_sql.strip(), flags=re.IGNORECASE)
            and "RETURNING" not in pg_sql.upper()
            and "ON CONFLICT" not in pg_sql.upper()
        )
        if is_insert_with_id:
            pg_sql = pg_sql.rstrip().rstrip(";") + " RETURNING id"

        if params is not None:
            self._cur.execute(pg_sql, tuple(params))
        else:
            self._cur.execute(pg_sql)

        if is_insert_with_id:
            row = self._cur.fetchone()
            self.lastrowid = row[0] if row else None
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self._cur.description]
        return HybridRow(cols, row)

    def fetchall(self):
        rows = self._cur.fetchall()
        if not rows or not self._cur.description:
            return []
        cols = [desc[0] for desc in self._cur.description]
        return [HybridRow(cols, r) for r in rows]


class PostgresConnectionWrapper:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self):
        return PostgresCursorWrapper(self._conn.cursor())

    def execute(self, sql: str, params: Any = None):
        cur = self.cursor()
        return cur.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_db_connection():
    """
    若設定了環境變數 DATABASE_URL (postgres:// 或 postgresql://)，自動連線至雲端 PostgreSQL (如 Neon / Supabase)；
    否則自動使用本機 SQLite (data/lunch.db)，確保雲端永久保存與本機一鍵啟動雙模式無縫相容。
    """
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url.startswith("postgres://") or db_url.startswith("postgresql://"):
        try:
            import psycopg
            raw_conn = psycopg.connect(db_url)
            return PostgresConnectionWrapper(raw_conn)
        except ImportError:
            import psycopg2
            raw_conn = psycopg2.connect(db_url)
            return PostgresConnectionWrapper(raw_conn)

    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(force_reseed: bool = False):
    use_pg = is_postgres_mode()
    if not use_pg:
        os.makedirs(DB_DIR, exist_ok=True)

    conn = get_db_connection()
    cursor = conn.cursor()

    if force_reseed:
        cursor.execute("DROP TABLE IF EXISTS orders")
        cursor.execute("DROP TABLE IF EXISTS weekly_schedules")
        cursor.execute("DROP TABLE IF EXISTS feedbacks")
        cursor.execute("DROP TABLE IF EXISTS menu_items")
        cursor.execute("DROP TABLE IF EXISTS restaurants")
        cursor.execute("DROP TABLE IF EXISTS system_settings")

    pk_type = "SERIAL PRIMARY KEY" if use_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"

    # 1. 餐廳名單
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS restaurants (
        id {pk_type},
        name TEXT NOT NULL UNIQUE,
        category TEXT NOT NULL,
        phone TEXT,
        min_order INTEGER DEFAULT 0,
        notes TEXT,
        image_url TEXT DEFAULT '',
        closed_days TEXT DEFAULT '',
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 2. 菜單項目
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS menu_items (
        id {pk_type},
        restaurant_id INTEGER NOT NULL,
        sub_category TEXT DEFAULT '主要餐點',
        name TEXT NOT NULL,
        variant TEXT DEFAULT '',
        price INTEGER NOT NULL,
        description TEXT,
        is_available INTEGER DEFAULT 1,
        FOREIGN KEY (restaurant_id) REFERENCES restaurants (id) ON DELETE CASCADE
    )
    """)

    # 3. 同學回饋紀錄
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS feedbacks (
        id {pk_type},
        restaurant_id INTEGER NOT NULL,
        seat_number TEXT,
        student_name TEXT,
        rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
        comment TEXT,
        tags TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (restaurant_id) REFERENCES restaurants (id) ON DELETE CASCADE
    )
    """)

    # 4. 每週排餐紀錄
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS weekly_schedules (
        id {pk_type},
        week_start_date TEXT NOT NULL,
        day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 1 AND 5),
        restaurant_id INTEGER NOT NULL,
        is_manual_override INTEGER DEFAULT 0,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(week_start_date, day_of_week),
        FOREIGN KEY (restaurant_id) REFERENCES restaurants (id) ON DELETE CASCADE
    )
    """)

    # 5. 點餐訂單
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS orders (
        id {pk_type},
        order_date TEXT NOT NULL,
        seat_number TEXT NOT NULL,
        student_name TEXT NOT NULL,
        dish_name TEXT NOT NULL,
        price INTEGER NOT NULL DEFAULT 0,
        quantity INTEGER NOT NULL DEFAULT 1,
        notes TEXT,
        is_paid INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 6. 系統設定
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    conn.commit()

    # 確保既有資料庫補齊欄位並同步 15 家真實店家完整菜單
    try:
        from .menu_data import ensure_variant_column, apply_real_menus
    except ImportError:
        from menu_data import ensure_variant_column, apply_real_menus

    ensure_variant_column(conn)

    cursor.execute("SELECT COUNT(*) FROM restaurants")
    rest_count = cursor.fetchone()[0]
    if rest_count < 15 or force_reseed:
        apply_real_menus(conn)

    conn.close()


if __name__ == "__main__":
    init_db(force_reseed=True)
    print("Database initialized and reseeded successfully.")
