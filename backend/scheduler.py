import random
from typing import List, Dict, Any, Optional, Tuple
import sqlite3
try:
    from .database import get_db_connection
except ImportError:
    from database import get_db_connection

def calculate_restaurant_scores(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    根據同學的回饋評分與留言次數，計算各餐廳的推薦分數
    """
    cursor = conn.cursor()
    # 抓取所有啟用的餐廳 (含公休日 closed_days)
    cursor.execute("""
        SELECT r.id, r.name, r.category, r.phone, r.min_order, r.notes,
               COALESCE(r.closed_days, '') as closed_days,
               COALESCE(AVG(f.rating), 4.0) as avg_rating,
               COUNT(f.id) as feedback_count
        FROM restaurants r
        LEFT JOIN feedbacks f ON r.id = f.restaurant_id
        WHERE r.is_active = 1
        GROUP BY r.id
    """)
    rows = cursor.fetchall()

    restaurants = []
    for row in rows:
        avg_rating = round(float(row["avg_rating"]), 2)
        count = int(row["feedback_count"])

        # 解析公休日數字列表 (1=週一 ... 7=週日)
        raw_closed = str(row["closed_days"] or "").strip()
        closed_set = set()
        if raw_closed:
            for part in raw_closed.replace(";", ",").split(","):
                part = part.strip()
                if part.isdigit():
                    closed_set.add(int(part))

        # 基礎計分公式：
        # - 平均評分 (權重 20)
        # - 回饋評論數量加成 (同學常點且討論熱烈，每則回饋 +1.5 分，最高 +15 分)
        # - 加上小幅度隨機擾動 (0~3 分)，避免永遠排同一組餐廳，增加點餐多樣性與驚喜
        composite_score = (avg_rating * 20.0) + min(count * 1.5, 15.0) + (random.random() * 3.0)

        restaurants.append({
            "id": row["id"],
            "name": row["name"],
            "category": row["category"],
            "phone": row["phone"],
            "min_order": row["min_order"],
            "notes": row["notes"],
            "closed_days": raw_closed,
            "closed_set": closed_set,
            "avg_rating": avg_rating,
            "feedback_count": count,
            "score": composite_score
        })

    # 依分數由高到低排序
    restaurants.sort(key=lambda x: x["score"], reverse=True)
    return restaurants

def validate_weekly_schedule(schedule_days: List[Dict[str, Any]], restaurant_meta: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    驗證排餐規則：
    1. 每周同一間店只能出現一次 (No duplicate restaurants in Mon-Fri)
    2. 同類型店家不連續兩天出現 (No consecutive same category)
    3. 店家公休日迴避檢查 (餐廳在該天公休時不可排餐)
    """
    errors = []
    warnings = []

    # 排序確保按 day_of_week 1..5
    sorted_days = sorted(schedule_days, key=lambda d: d.get("day_of_week", 0))

    seen_restaurants = {}
    prev_category = None
    prev_day_name = None

    day_names = {1: "週一", 2: "週二", 3: "週三", 4: "週四", 5: "週五"}

    for day in sorted_days:
        day_num = day.get("day_of_week")
        day_label = day_names.get(day_num, f"第{day_num}天")
        r_id = day.get("restaurant_id")
        r_name = day.get("restaurant_name") or f"餐廳ID {r_id}"
        cat = day.get("category")

        if not r_id:
            errors.append(f"{day_label} 尚未指派餐廳。")
            continue

        # 規則 1：每週同店不可重複
        if r_id in seen_restaurants:
            errors.append(
                f"違反規則「每週同店不可重複」：店家【{r_name}】同時出現在 {seen_restaurants[r_id]} 與 {day_label}！"
            )
        else:
            seen_restaurants[r_id] = day_label

        # 規則 2：同類型店家不可連續兩天出現
        if prev_category and cat and prev_category.strip() == cat.strip():
            errors.append(
                f"違反規則「同類型店家不連續兩天出現」：{prev_day_name} 與 {day_label} 連續兩天皆為【{cat}】類型店家！"
            )

        # 規則 3：公休日檢查
        closed_days_raw = str(day.get("closed_days") or "").strip()
        if not closed_days_raw and restaurant_meta and r_id in restaurant_meta:
            closed_days_raw = str(restaurant_meta[r_id].get("closed_days") or "").strip()

        if closed_days_raw and day_num:
            closed_parts = [p.strip() for p in closed_days_raw.replace(";", ",").split(",") if p.strip().isdigit()]
            if str(day_num) in closed_parts:
                errors.append(
                    f"違反規則「避開公休日」：店家【{r_name}】{day_label}為公休日，不可排入供餐！"
                )

        prev_category = cat
        prev_day_name = day_label

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings
    }

def auto_generate_weekly_plan(conn: sqlite3.Connection, last_friday_category: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """
    智慧自動排餐演算法：
    使用回溯法 (Backtracking) 尋找滿足所有約束且滿意度分數最高的 5 天排餐組合。
    """
    candidates = calculate_restaurant_scores(conn)

    if len(candidates) < 5:
        # 餐廳總數少於 5 間，無法滿足每週 5 天不重複規則
        return None

    best_schedule = None
    best_total_score = -1.0

    # 5 天回溯搜尋
    # current_schedule: list of 5 restaurants
    def backtrack(day_idx: int, current_selection: List[Dict[str, Any]], used_ids: set, current_score: float):
        nonlocal best_schedule, best_total_score

        if day_idx == 5:
            if current_score > best_total_score:
                best_total_score = current_score
                best_schedule = list(current_selection)
            return

        prev_category = None
        if day_idx == 0:
            prev_category = last_friday_category
        else:
            prev_category = current_selection[-1]["category"]

        for rest in candidates:
            # 檢查約束 1：同週不重複
            if rest["id"] in used_ids:
                continue

            # 檢查約束 2：同類型不連續
            if prev_category and rest["category"] == prev_category:
                continue

            # 檢查約束 3：避開公休日 (day_idx 0..4 對應週一至週五 1..5)
            day_dow = day_idx + 1
            if day_dow in rest.get("closed_set", set()):
                continue

            # 剪枝：若即使後續天數都拿到最高分也無法超越當前 best，可提前終止
            # 由於天數僅 5 天，完全搜尋極為快速 (< 10ms)

            used_ids.add(rest["id"])
            current_selection.append(rest)

            backtrack(day_idx + 1, current_selection, used_ids, current_score + rest["score"])

            # 回溯還原
            current_selection.pop()
            used_ids.remove(rest["id"])

            # 若已找到高品質解且遍歷足夠，可快速返回最優解
            if best_schedule is not None and day_idx == 0 and len(current_selection) == 0:
                # 已經嘗試過最高分的幾個起點
                pass

    backtrack(0, [], set(), 0.0)

    # 若因為嚴格限制（如類別集中）導致找不到完全解，嘗試放寬最後週五與週一的銜接
    if best_schedule is None and last_friday_category is not None:
        return auto_generate_weekly_plan(conn, last_friday_category=None)

    if best_schedule:
        day_names = ["週一", "週二", "週三", "週四", "週五"]
        result = []
        for i, rest in enumerate(best_schedule):
            result.append({
                "day_of_week": i + 1,
                "day_name": day_names[i],
                "restaurant_id": rest["id"],
                "restaurant_name": rest["name"],
                "category": rest["category"],
                "phone": rest["phone"],
                "closed_days": rest.get("closed_days", ""),
                "avg_rating": rest["avg_rating"],
                "score": rest["score"],
                "is_manual_override": 0,
                "notes": f"系統依同學評分({rest['avg_rating']}★)智慧排定"
            })
        return result

    return None
