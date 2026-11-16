import unittest
import sqlite3
import os
import shutil
from datetime import datetime
from backend.database import init_db, get_db_connection, DB_PATH
from backend.scheduler import calculate_restaurant_scores, validate_weekly_schedule, auto_generate_weekly_plan
from backend.time_window import check_ordering_window

class TestSchedulerAndWindow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_database_seeding(self):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM restaurants")
        count = cursor.fetchone()[0]
        self.assertGreaterEqual(count, 5, "至少應有 5 家可供每週排餐之餐廳")
        conn.close()

    def test_auto_generate_weekly_plan_constraints(self):
        conn = get_db_connection()
        # 產生 10 次排餐，確保每次都完全滿足硬性條件
        for _ in range(10):
            plan = auto_generate_weekly_plan(conn)
            self.assertIsNotNone(plan, "應成功產出 5 天排程")
            self.assertEqual(len(plan), 5, "必須包含週一至週五共 5 天")

            validation = validate_weekly_schedule(plan)
            self.assertTrue(validation["valid"], f"排程應通過驗證，但出現錯誤: {validation['errors']}")

            # 驗證約束 1：同週無重複店家
            restaurant_ids = [d["restaurant_id"] for d in plan]
            self.assertEqual(len(set(restaurant_ids)), 5, "每週同一間店只能出現一次")

            # 驗證約束 2：相鄰天不連續同類型
            categories = [d["category"] for d in plan]
            for i in range(len(categories) - 1):
                self.assertNotEqual(categories[i], categories[i+1],
                                    f"第 {i+1} 天與第 {i+2} 天類型相同: {categories[i]}")

        conn.close()

    def test_schedule_validation_detects_violations(self):
        # 測試：故意放入重複店家
        invalid_plan_dup = [
            {"day_of_week": 1, "restaurant_id": 1, "restaurant_name": "金園排骨", "category": "便當"},
            {"day_of_week": 2, "restaurant_id": 2, "restaurant_name": "老街牛肉麵", "category": "麵食"},
            {"day_of_week": 3, "restaurant_id": 1, "restaurant_name": "金園排骨", "category": "便當"},
            {"day_of_week": 4, "restaurant_id": 3, "restaurant_name": "吉野家", "category": "日式"},
            {"day_of_week": 5, "restaurant_id": 4, "restaurant_name": "活力健康餐", "category": "健康餐"},
        ]
        val1 = validate_weekly_schedule(invalid_plan_dup)
        self.assertFalse(val1["valid"])
        self.assertTrue(any("每週同店不可重複" in err for err in val1["errors"]))

        # 測試：故意連續兩天排同類型店家 (便當 -> 便當)
        invalid_plan_consec = [
            {"day_of_week": 1, "restaurant_id": 1, "restaurant_name": "金園排骨", "category": "便當"},
            {"day_of_week": 2, "restaurant_id": 8, "restaurant_name": "正宗燒臘", "category": "便當"},
            {"day_of_week": 3, "restaurant_id": 2, "restaurant_name": "老街牛肉麵", "category": "麵食"},
            {"day_of_week": 4, "restaurant_id": 3, "restaurant_name": "吉野家", "category": "日式"},
            {"day_of_week": 5, "restaurant_id": 5, "restaurant_name": "香濃咖哩", "category": "咖哩"},
        ]
        val2 = validate_weekly_schedule(invalid_plan_consec)
        self.assertFalse(val2["valid"])
        self.assertTrue(any("同類型店家不連續兩天出現" in err for err in val2["errors"]))

        # 測試：故意將週一公休的店家排在週一 (day_of_week=1)
        invalid_plan_closed = [
            {"day_of_week": 1, "restaurant_id": 1, "restaurant_name": "週一公休店", "category": "便當", "closed_days": "1"},
            {"day_of_week": 2, "restaurant_id": 2, "restaurant_name": "老街牛肉麵", "category": "麵食", "closed_days": ""},
            {"day_of_week": 3, "restaurant_id": 3, "restaurant_name": "吉野家", "category": "日式", "closed_days": ""},
            {"day_of_week": 4, "restaurant_id": 4, "restaurant_name": "活力健康餐", "category": "健康餐", "closed_days": ""},
            {"day_of_week": 5, "restaurant_id": 5, "restaurant_name": "香濃咖哩", "category": "咖哩", "closed_days": ""},
        ]
        val3 = validate_weekly_schedule(invalid_plan_closed)
        self.assertFalse(val3["valid"])
        self.assertTrue(any("避開公休日" in err for err in val3["errors"]))

    def test_ordering_time_windows(self):
        # 1. 週日 16:30 -> 開放訂週一午餐
        sun_evening = datetime(2026, 11, 8, 16, 30) # Sunday
        res = check_ordering_window(sun_evening)
        self.assertTrue(res["is_open"])
        self.assertEqual(res["target_day_of_week"], 1) # 週一
        self.assertEqual(res["target_meal_date"], "2026-11-09")

        # 2. 週一 08:30 -> 開放訂週一午餐
        mon_morning = datetime(2026, 11, 9, 8, 30) # Monday morning
        res = check_ordering_window(mon_morning)
        self.assertTrue(res["is_open"])
        self.assertEqual(res["target_day_of_week"], 1)
        self.assertEqual(res["target_meal_date"], "2026-11-09")

        # 3. 週一 11:00 -> 截止，未到 16:00
        mon_noon = datetime(2026, 11, 9, 11, 0)
        res = check_ordering_window(mon_noon)
        self.assertFalse(res["is_open"])

        # 4. 週四 20:00 -> 開放訂週五午餐
        thu_night = datetime(2026, 11, 12, 20, 0) # Thursday
        res = check_ordering_window(thu_night)
        self.assertTrue(res["is_open"])
        self.assertEqual(res["target_day_of_week"], 5) # 週五
        self.assertEqual(res["target_meal_date"], "2026-11-13")

        # 5. 週五 18:00 -> 週末不開放
        fri_night = datetime(2026, 11, 13, 18, 0) # Friday
        res = check_ordering_window(fri_night)
        self.assertFalse(res["is_open"])

    def test_admin_hmac_token_and_route_security(self):
        from backend.main import create_admin_token, verify_admin_token, require_admin
        from fastapi import HTTPException

        # 1. 未帶 Authorization header 時應拋出 401
        with self.assertRaises(HTTPException) as ctx1:
            require_admin(None)
        self.assertEqual(ctx1.exception.status_code, 401)

        # 2. 偽造的 Bearer token 應拋出 401
        with self.assertRaises(HTTPException) as ctx2:
            require_admin("Bearer fake.token.sig")
        self.assertEqual(ctx2.exception.status_code, 401)

        # 3. 合法簽發的 HMAC token 應通過驗證
        valid_token = create_admin_token()
        self.assertTrue(verify_admin_token(valid_token))
        self.assertTrue(require_admin(f"Bearer {valid_token}"))

if __name__ == "__main__":
    unittest.main()
