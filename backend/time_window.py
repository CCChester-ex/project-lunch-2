from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

TW_TZ = timezone(timedelta(hours=8))

WEEKDAY_NAMES = {
    1: "週一",
    2: "週二",
    3: "週三",
    4: "週四",
    5: "週五",
    6: "週六",
    7: "週日"
}

def get_now_taipei() -> datetime:
    """取得台灣時區 (UTC+8) 的當前時間 (naive datetime)，確保在雲端主機 (Render) 上時間不會慢 8 小時"""
    return datetime.now(TW_TZ).replace(tzinfo=None)

def get_current_time(simulated_time_str: Optional[str] = None) -> datetime:
    """取得目前時間（若有設定模擬時間則使用模擬時間，否則使用台灣時間 UTC+8）"""
    if simulated_time_str and simulated_time_str.strip():
        try:
            return datetime.fromisoformat(simulated_time_str.strip())
        except Exception:
            pass
    return get_now_taipei()

def check_ordering_window(now: Optional[datetime] = None, allow_test_override: bool = False) -> Dict[str, Any]:
    """
    判斷當前是否在訂餐時段內：
    - 週日 16:00 ~ 週一 10:00 -> 訂週一午餐
    - 週一 16:00 ~ 週二 10:00 -> 訂週二午餐
    - 週二 16:00 ~ 週三 10:00 -> 訂週三午餐
    - 週三 16:00 ~ 週四 10:00 -> 訂週四午餐
    - 週四 16:00 ~ 週五 10:00 -> 訂週五午餐
    - 其餘時間非訂餐時段（如白天 10:00~16:00、週五 10:00 至週日 16:00 週末放假）
    """
    if now is None:
        now = get_now_taipei()

    # weekday: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute
    second = now.second

    is_open = False
    target_date = None
    target_day_of_week = None
    closes_at = None
    next_opens_at = None
    message = ""

    # 1. 晚間時段：16:00 ~ 23:59:59
    if hour >= 16:
        if weekday == 6:  # 週日晚上 -> 訂週一 (隔天)
            is_open = True
            target_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            target_day_of_week = 1
            closes_at = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        elif weekday in (0, 1, 2, 3):  # 週一~週四晚上 -> 訂隔天 (週二~週五)
            is_open = True
            target_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            target_day_of_week = weekday + 2  # 0(Mon)->2(Tue), 1(Tue)->3(Wed), 2(Wed)->4(Thu), 3(Thu)->5(Fri)
            closes_at = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        elif weekday == 4:  # 週五晚上 -> 週末無午餐
            is_open = False
            # 下次開放：本週日 16:00 (2天後)
            next_opens_at = (now + timedelta(days=2)).replace(hour=16, minute=0, second=0, microsecond=0)
            message = "週五晚間不開放訂餐，下週一午餐將於週日 16:00 開放預訂。"
        elif weekday == 5:  # 週六晚上
            is_open = False
            # 下次開放：明天週日 16:00
            next_opens_at = (now + timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
            message = "週末放假中，下週一午餐將於週日 16:00 開放預訂。"

    # 2. 清晨至上午時段：00:00 ~ 10:00:00
    elif (hour < 10) or (hour == 10 and minute == 0 and second == 0):
        if weekday in (0, 1, 2, 3, 4):  # 週一至週五上午 10:00 前 -> 訂當天午餐
            is_open = True
            target_date = now.strftime("%Y-%m-%d")
            target_day_of_week = weekday + 1  # 0(Mon)->1, 4(Fri)->5
            closes_at = now.replace(hour=10, minute=0, second=0, microsecond=0)
        else:  # 週六或週日早上
            is_open = False
            days_until_sunday = 6 - weekday if weekday == 5 else 0
            next_opens_at = (now + timedelta(days=days_until_sunday)).replace(hour=16, minute=0, second=0, microsecond=0)
            message = "週末放假中，下週一午餐將於週日 16:00 開放預訂。"

    # 3. 中午及下午時段：10:00:01 ~ 15:59:59
    else:
        is_open = False
        if weekday in (0, 1, 2, 3):  # 週一~週四下午 -> 今日上午已截止，今日 16:00 開放訂隔天
            next_opens_at = now.replace(hour=16, minute=0, second=0, microsecond=0)
            target_tomorrow_day = WEEKDAY_NAMES[weekday + 2]
            message = f"今日午餐訂購已截止。預計於今日 16:00 開放預訂【{target_tomorrow_day}】午餐。"
        elif weekday == 4:  # 週五下午 -> 週末，週日 16:00 開放
            next_opens_at = (now + timedelta(days=2)).replace(hour=16, minute=0, second=0, microsecond=0)
            message = "今日午餐訂購已截止，下週一午餐將於週日 16:00 開放預訂。"
        elif weekday == 5:  # 週六下午
            next_opens_at = (now + timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
            message = "週末放假中，下週一午餐將於週日 16:00 開放預訂。"
        elif weekday == 6:  # 週日下午 10:00~16:00
            next_opens_at = now.replace(hour=16, minute=0, second=0, microsecond=0)
            message = "今日 16:00 將準時開放預訂【週一】午餐！"

    # 若處於測試模式且本來為關閉，可彈性開放並設定預設目標日（便於展示與測試）
    original_is_open = is_open
    if not is_open and allow_test_override:
        is_open = True
        # 決定一個適合的目標日期
        if weekday in (0, 1, 2, 3, 4):
            target_day_of_week = weekday + 1
            target_date = now.strftime("%Y-%m-%d")
        else:
            target_day_of_week = 1
            # 找到最近的週一
            days_ahead = (0 - weekday) % 7
            if days_ahead == 0:
                days_ahead = 7
            target_date = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        message = "【測試模式生效中】已自動開放點餐，讓您可直接測試菜名輸入與下單流程。"

    remaining_seconds = 0
    if is_open and closes_at:
        remaining_seconds = max(0, int((closes_at - now).total_seconds()))
    elif not is_open and next_opens_at:
        remaining_seconds = max(0, int((next_opens_at - now).total_seconds()))

    target_day_name = WEEKDAY_NAMES.get(target_day_of_week, "")

    return {
        "is_open": is_open,
        "original_is_open": original_is_open,
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "target_meal_date": target_date,
        "target_day_of_week": target_day_of_week,
        "target_day_name": target_day_name,
        "message": message or (f"目前開放預訂【{target_day_name} {target_date}】午餐！請於隔日 10:00 前完成點餐。" if is_open else "非訂餐時段"),
        "time_remaining_seconds": remaining_seconds,
        "closes_at": closes_at.strftime("%Y-%m-%d %H:%M:%S") if closes_at else None,
        "next_opens_at": next_opens_at.strftime("%Y-%m-%d %H:%M:%S") if next_opens_at else None
    }

def get_week_monday(date_obj: datetime) -> str:
    """取得給定日期該週週一的日期字串 (YYYY-MM-DD)"""
    monday = date_obj - timedelta(days=date_obj.weekday())
    return monday.strftime("%Y-%m-%d")

def get_active_week_monday(now: Optional[datetime] = None) -> str:
    """
    計算目前作用中的學校排餐與點餐週（週一日期 YYYY-MM-DD）：
    - 週一至週四全天、週五上午 10:00 前：作用週為「本週」週一
    - 週五上午 10:00 後、週六、週日全天：本週學校午餐已結束，下一個供餐週為「次週」！
      因此作用週自動切換至「次週週一」，讓同學在週日 16:00 開放時能看到下週一至週五（如 11/09~11/13），
      不再被卡在已經過期的上週（11/02~11/06）。
    """
    if now is None:
        now = get_now_taipei()

    weekday = now.weekday()  # 0=Mon, ..., 4=Fri, 5=Sat, 6=Sun
    if weekday == 6:  # 週日 -> 次週一 (明天)
        monday = now + timedelta(days=1)
    elif weekday == 5:  # 週六 -> 次週一 (後天)
        monday = now + timedelta(days=2)
    elif weekday == 4 and (now.hour >= 10):  # 週五 10:00 截止後 -> 次週一 (3天後)
        monday = now + timedelta(days=3)
    else:  # 週一至週五 10:00 前 -> 當週週一
        monday = now - timedelta(days=weekday)

    return monday.strftime("%Y-%m-%d")
