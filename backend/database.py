import sqlite3
import os
from datetime import datetime, timedelta

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "lunch.db")

def get_db_connection():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(force_reseed: bool = False):
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

    # 1. 餐廳名單 (新增 image_url 支援查看紙本實體菜單圖、closed_days 支援每週固定公休日，如 '6,7' 或 '1')
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS restaurants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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

    # 確保既有資料庫平滑升級包含 closed_days 欄位
    cursor.execute("PRAGMA table_info(restaurants)")
    cols = [r["name"] for r in cursor.fetchall()]
    if "closed_days" not in cols:
        cursor.execute("ALTER TABLE restaurants ADD COLUMN closed_days TEXT DEFAULT ''")

    # 2. 菜單項目 (新增 sub_category 次分類欄位，如炒飯類、鍋燒類、湯品點心)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS menu_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS feedbacks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS weekly_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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

    # 確保既有資料庫補齊 variant 欄位並同步 15 家真實店家完整菜單
    try:
        from .menu_data import ensure_variant_column, apply_real_menus
    except ImportError:
        from menu_data import ensure_variant_column, apply_real_menus
    ensure_variant_column(conn)

    cursor.execute("SELECT COUNT(*) FROM restaurants")
    rest_count = cursor.fetchone()[0]
    if rest_count == 0:
        seed_comprehensive_data(cursor, conn)
        apply_real_menus(conn)
    elif rest_count < 15:
        apply_real_menus(conn)

    conn.close()

def seed_comprehensive_data(cursor, conn):
    """
    匯入包含 4 家真實上傳菜單照片的豐富店家與菜單資料：
    1. 成大館 外帶單 (炒飯/炒麵/鍋燒/湯品/小菜，超過 50+ 道菜)
    2. 義之饌 (紅醬/白醬/青醬/蟹醬/咖哩/粉紅醬/點心湯品，超過 60+ 道菜)
    3. 麥 平價經典麵食 (豚骨/乾拌麵/鍋燒/炒泡麵/小菜，超過 40+ 道菜)
    4. 佳味庭食堂 (便當/丼飯/雙拼，大份量學生愛店)
    5. 金園排骨便當 (經典便當)
    6. 吉野家日式丼飯 (連鎖日式)
    7. 活力輕食健康餐盒 (健康餐)
    8. 美式漢堡速食客 (異國美式)
    """

    restaurants_data = [
        # --- 1. 成大館 (真實菜單照片) ---
        {
            "name": "成大館 外帶單",
            "category": "便當快炒",
            "phone": "0931-215-876",
            "min_order": 300,
            "image_url": "/static/menus/cheng_da_guan.jpg",
            "notes": "育樂街38號。加飯、麵不加價(事先告知)！起司加10元、意麵/泡麵加15元。",
            "sub_categories": {
                "炒飯類": [
                    ("肉絲蛋炒飯", 80, "經典熱炒粒粒分明"),
                    ("照燒豬肉炒飯", 85, "特調照燒甜鹹醬香"),
                    ("照燒牛肉炒飯", 95, "鮮嫩牛五花搭配照燒醬"),
                    ("蒜香奶油豬肉炒飯", 85, "濃郁奶油與蒜香大火快炒"),
                    ("蒜香奶油牛肉炒飯", 95, "蒜香與牛肉完美融合"),
                    ("培根蛋炒飯", 85, "香脆培根油香四溢"),
                    ("韓式辣醬豬肉炒飯", 85, "道地韓式辣醬風味"),
                    ("韓式辣醬牛肉炒飯", 95, "香辣過癮"),
                    ("沙茶豬肉炒飯", 85, "經典台式牛頭牌沙茶香"),
                    ("沙茶牛肉炒飯", 95, "人氣第一名招牌"),
                    ("招牌香辣豬肉炒飯", 85, "獨特香辣微麻層次豐富"),
                    ("咖哩豬肉炒飯", 85, "南洋咖哩香氣誘人"),
                    ("蕃茄牛肉炒飯", 100, "酸甜開胃"),
                    ("韓式泡菜豬肉炒飯", 90, "酸辣泡菜爽脆解膩"),
                    ("泰式塔香豬肉炒飯", 90, "九層塔香氣爆棚"),
                    ("泰式打拋豬肉炒飯", 90, "泰式香辣下飯"),
                    ("XO醬牛肉炒飯", 100, "特級海味XO醬炒製"),
                    ("川味獨門剁椒牛肉炒飯", 100, "嗜辣同學必點")
                ],
                "炒麵類": [
                    ("肉絲炒麵", 80, "傳統熱炒拉麵"),
                    ("培根炒麵", 85, "香氣撲鼻"),
                    ("沙茶牛肉炒麵", 95, "經典沙茶醬炒牛肉"),
                    ("韓式泡菜豬肉炒麵", 90, "微酸微辣"),
                    ("蒜辣豬肉炒麵", 90, "重口味首選")
                ],
                "鍋燒類": [
                    ("原味鍋燒意麵", 80, "大骨鮮甜高湯，附蛋、肉片與青菜"),
                    ("沙茶鍋燒意麵", 85, "濃香沙茶風味"),
                    ("起司奶油鍋燒意麵", 90, "奶香牽絲，女同學最愛"),
                    ("麻辣鍋燒意麵", 90, "川味麻辣湯底"),
                    ("韓式泡菜鍋燒意麵", 90, "酸辣夠味好喝")
                ],
                "丼飯類": [
                    ("日式豬肉丼", 85, "洋蔥甜醬汁與半熟滑蛋"),
                    ("日式牛肉丼", 95, "牛丼大份量滿滿牛肉片"),
                    ("韓式辣醬豬肉丼", 90, "酸甜香辣下飯"),
                    ("咖哩豬肉丼", 90, "日式濃咖哩"),
                    ("起司牛肉丼", 105, "覆蓋雙倍起司熔岩")
                ],
                "炒泡麵與小菜湯品": [
                    ("醬香炒泡麵", 80, "人氣街頭炒泡麵"),
                    ("香辣炒泡麵", 85, "微辣過癮"),
                    ("香煎荷包蛋", 20, "半熟/全熟可備註"),
                    ("日式海帶絲", 35, "爽脆開胃小菜"),
                    ("煙燻豬耳朵", 40, "煙燻香味"),
                    ("蛋花湯", 35, "家常溫暖好湯"),
                    ("貢丸湯", 35, "新竹大貢丸兩顆"),
                    ("蛤蜊湯", 45, "鮮甜現煮蛤蜊")
                ]
            }
        },

        # --- 2. 義之饌 (真實菜單照片) ---
        {
            "name": "義之饌（西門店）",
            "category": "異國料理",
            "phone": "06-2525900",
            "min_order": 300,
            "image_url": "/static/menus/yi_zhi_chuan.jpg",
            "notes": "點餐說明：餐點可選直麵、管麵或燉飯；加飯或麵加10元。焗烤加25元；雙份起司加35元。價格格式標註為：（原價 / 團體訂購價）。單次外送滿300元即送，全班團體享有團體優惠價！",
            "sub_categories": {
                "茄汁紅醬系列": [
                    ("義大利肉醬麵", 65, "慢火熬煮牛豬肉醬（團體特惠價）"),
                    ("茄汁蘑菇麵/飯", 65, "新鮮蘑菇與酸甜番茄"),
                    ("茄汁培根麵/飯", 75, "鹹香培根與濃茄汁"),
                    ("茄汁德國香腸麵/飯", 80, "脆皮德式香腸"),
                    ("茄汁燻雞麵/飯", 80, "嫩燻雞肉"),
                    ("茄汁白酒蛤蜊麵/飯", 85, "新鮮蛤蜊海味鮮甜"),
                    ("茄汁雞米花麵/飯", 85, "現炸香酥雞米花"),
                    ("茄汁中卷麵/飯", 95, "Q彈鮮甜中卷"),
                    ("茄汁總匯海鮮麵/飯", 95, "白蝦、蛤蜊、中卷一次滿足"),
                    ("茄汁雞塊麵/飯", 95, "酥炸大雞塊")
                ],
                "奶油白醬系列": [
                    ("奶油蘑菇麵/飯", 65, "香濃白醬醇厚順口"),
                    ("奶油培根麵/飯", 75, "經典培根白醬"),
                    ("奶油德國香腸麵/飯", 80, "德腸與濃郁白醬"),
                    ("奶油田園鮮蔬麵/飯", 75, "彩椒、花椰菜與蘑菇"),
                    ("奶油白酒蛤蜊麵/飯", 85, "蛤蜊精華溶入白醬"),
                    ("奶油雞米花麵/飯", 85, "炸雞搭配香濃白醬"),
                    ("奶油中卷麵/飯", 95, "海鮮控必吃"),
                    ("奶油總匯海鮮麵/飯", 95, "滿滿海鮮料")
                ],
                "羅勒青醬系列": [
                    ("青醬蘑菇麵/飯", 70, "自製新鮮九層塔松子青醬"),
                    ("青醬培根麵/飯", 80, "翠綠青醬香濃開胃"),
                    ("青醬白酒蛤蜊麵/飯", 85, "人氣熱門第一名"),
                    ("青醬雞米花麵/飯", 95, "青醬配香酥炸雞"),
                    ("青醬總匯海鮮麵/飯", 95, "豪華大份量海鮮")
                ],
                "美味蟹醬與粉紅醬": [
                    ("蟹醬義大利麵", 70, "特調濃醇蟹香醬汁"),
                    ("蟹醬培根麵/飯", 80, "蟹香與培根香交織"),
                    ("蟹醬總匯海鮮麵/飯", 95, "整盤滿滿蟹肉鮮甜"),
                    ("粉紅醬中卷麵/飯", 100, "紅醬酸甜融合白醬奶香"),
                    ("粉紅醬海鮮麵/飯", 105, "超高人氣粉紅醬")
                ],
                "湯品與可口點心": [
                    ("玉米濃湯", 25, "香甜滑順玉米粒濃湯"),
                    ("蛤蜊玉米濃湯", 35, "鮮蛤蜊加料升級"),
                    ("笑臉薯餅", 35, "可愛酥脆金黃薯餅"),
                    ("香酥雞塊", 35, "金黃脆皮雞塊"),
                    ("雞米花", 35, "微辣香酥雞米花"),
                    ("香炸德國香腸", 35, "多汁彈牙")
                ]
            }
        },

        # --- 3. 麥面 Men (真實菜單照片) ---
        {
            "name": "麥面 Men",
            "category": "麵食",
            "phone": "06-208-6288",
            "min_order": 300,
            "image_url": "/static/menus/mai_noodles.jpg",
            "notes": "備註說明：拉麵、烏龍麵加麵不加價；加飯、泡麵、雞絲、意麵、冬粉加量加10元。育樂街135號。",
            "sub_categories": {
                "豚骨系列": [
                    ("招牌豚骨拉麵", 90, "濃郁豚骨白湯，附叉燒、溏心蛋、海苔"),
                    ("豚骨烏龍麵", 90, "Q彈讚岐烏龍麵條"),
                    ("綠咖哩拉麵", 95, "南洋微辣綠咖哩湯頭"),
                    ("麻辣豚骨拉麵", 95, "麻香微辣超開胃"),
                    ("香濃起司拉麵", 100, "起司片融入豚骨湯"),
                    ("蒜麻豚骨拉麵", 100, "濃郁蒜香與特製麻油")
                ],
                "乾拌麵系列": [
                    ("古早味乾拌麵", 90, "特調老滷肉燥與青菜"),
                    ("咖哩乾拌麵", 90, "日式濃郁咖哩醬拌麵"),
                    ("麻辣乾拌麵", 90, "麻辣醬汁香辣爽口"),
                    ("蒜香乾拌麵", 90, "濃郁蒜蓉醬拌麵"),
                    ("泰式酸辣乾拌麵", 90, "檸檬酸辣醬汁"),
                    ("蒜麻乾拌麵", 95, "蒜頭爆香配特調麻醬")
                ],
                "鍋燒系列": [
                    ("原味經典鍋燒", 75, "鮮甜柴魚高湯底，配料豐富"),
                    ("麻辣風味鍋燒", 75, "香辣過癮"),
                    ("沙茶風味鍋燒", 75, "經典濃郁沙茶"),
                    ("韓式泡菜鍋燒", 80, "正統韓式酸辣泡菜")
                ],
                "炒泡麵與小菜": [
                    ("古早味炒泡麵", 60, "特製醬汁熱炒泡麵"),
                    ("沙茶炒泡麵", 60, "沙茶濃香"),
                    ("泡菜炒泡麵", 65, "泡菜爽脆微酸"),
                    ("咖哩豬肉飯", 70, "不想吃麵的好選擇"),
                    ("起司咖哩豬肉飯", 80, "起司牽絲咖哩飯"),
                    ("日式溏心蛋", 25, "半熟流心蛋"),
                    ("開胃小黃瓜", 30, "涼拌酸甜爽口"),
                    ("私房滷豆干", 30, "入味多汁")
                ]
            }
        },

        # --- 4. 佳味庭食堂 (真實菜單照片) ---
        {
            "name": "佳味庭食堂",
            "category": "便當快炒",
            "phone": "06-238-6182",
            "min_order": 400,
            "image_url": "/static/menus/jia_wei_ting.jpg",
            "notes": "育樂街53之7。每週六公休。大份量學生最愛！可免費選擇：正常、飯多、飯少，備註欄註明即可。",
            "sub_categories": {
                "綜合餐點 (可選飯多)": [
                    ("紅酒燉豬肉便當", 100, "法式紅酒慢燉軟嫩梅花豬，附三樣當季配菜"),
                    ("墨西哥辣燉豬肉便當", 95, "異國香料微辣濃郁，非常下飯"),
                    ("香茅辣雞腿便當", 95, "東南亞香茅香辣大雞腿"),
                    ("印度卡麗雞肉飯", 95, "辛香濃郁印度風味咖哩"),
                    ("正宗海南雞飯", 95, "鮮嫩油香雞肉附特調蔥薑沾醬"),
                    ("薑汁燒豬肉便當", 95, "日式生薑燒肉甜香下飯"),
                    ("酥炸豬排咖哩飯", 90, "厚切炸豬排淋濃郁咖哩"),
                    ("卡啦咖哩蛋包飯", 100, "香脆卡啦大雞排配金黃滑嫩蛋包")
                ],
                "日式丼飯系列": [
                    ("特選豬排丼飯", 90, "特選里肌豬排裹滑嫩洋蔥蛋"),
                    ("卡啦雞腿排丼飯", 90, "外酥內嫩多汁雞腿排"),
                    ("日式親子丼飯", 85, "去骨雞腿肉配香甜洋蔥蛋液"),
                    ("黃金炸蝦丼飯", 95, "酥炸大草蝦三隻"),
                    ("香煎鯛魚丼飯", 95, "無刺鮮嫩台灣鯛魚排"),
                    ("炙烤秋刀魚丼飯", 90, "日式鹽烤秋刀魚整條"),
                    ("特選牛丼", 150, "雙倍牛肉五花份量十足"),
                    ("隨便丼飯-單拼(卡啦雞腿+炸蝦)", 90, "雙重主菜一次滿足超划算"),
                    ("隨便丼飯-雙拼(卡啦雞咖哩+卡啦雞丼)", 95, "超大胃王同學必吃")
                ]
            }
        },

    ]

    cursor.execute("SELECT COUNT(*) FROM feedbacks")
    if cursor.fetchone()[0] == 0:
        for rest_id, seat, student, rating, comment, tags in sample_feedbacks:
            cursor.execute("""
                INSERT INTO feedbacks (restaurant_id, seat_number, student_name, rating, comment, tags)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (rest_id, seat, student, rating, comment, tags))

    cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('test_mode', 'true')")
    cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('simulated_time', '')")

    conn.commit()

if __name__ == "__main__":
    init_db(force_reseed=True)
    print("Database reseeded successfully with full menus and uploaded photos at", DB_PATH)
