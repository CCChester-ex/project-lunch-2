from pydantic import BaseModel, Field
from typing import List, Optional

class MenuItemBase(BaseModel):
    name: str
    price: int
    sub_category: Optional[str] = "主要餐點"
    variant: Optional[str] = ""
    description: Optional[str] = ""
    is_available: Optional[int] = 1

class MenuItemCreate(MenuItemBase):
    pass

class MenuItemResponse(MenuItemBase):
    id: int
    restaurant_id: int

class RestaurantBase(BaseModel):
    name: str
    category: str
    phone: Optional[str] = ""
    min_order: Optional[int] = 0
    notes: Optional[str] = ""
    image_url: Optional[str] = ""
    closed_days: Optional[str] = ""
    is_active: Optional[int] = 1

class RestaurantCreate(RestaurantBase):
    menu_items: Optional[List[MenuItemCreate]] = []

class RestaurantResponse(RestaurantBase):
    id: int
    avg_rating: Optional[float] = 0.0
    feedback_count: Optional[int] = 0
    menu_items: Optional[List[MenuItemResponse]] = []

class FeedbackCreate(BaseModel):
    restaurant_id: int
    seat_number: Optional[str] = ""
    student_name: Optional[str] = ""
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = ""
    tags: Optional[str] = ""

class FeedbackResponse(FeedbackCreate):
    id: int
    restaurant_name: Optional[str] = ""
    created_at: str

class DayScheduleItem(BaseModel):
    day_of_week: int  # 1..5
    day_name: str     # 週一..週五
    target_date: str  # YYYY-MM-DD
    restaurant_id: Optional[int] = None
    restaurant_name: Optional[str] = None
    category: Optional[str] = None
    phone: Optional[str] = None
    image_url: Optional[str] = ""
    avg_rating: Optional[float] = 0.0
    menu_items: Optional[List[MenuItemResponse]] = []
    is_manual_override: Optional[int] = 0
    notes: Optional[str] = ""

class WeeklyScheduleSaveRequest(BaseModel):
    week_start_date: str
    days: List[DayScheduleItem]

class OrderItem(BaseModel):
    dish_name: str
    price: int
    quantity: int = 1
    notes: Optional[str] = ""

class OrderCreate(BaseModel):
    order_date: Optional[str] = ""
    seat_number: str
    student_name: str
    # 支援多品項點餐清單 (購物車)
    items: Optional[List[OrderItem]] = []
    # 相容舊版單品項
    dish_name: Optional[str] = None
    price: Optional[int] = None
    quantity: Optional[int] = 1
    notes: Optional[str] = ""

class OrderResponse(BaseModel):
    id: int
    order_date: str
    seat_number: str
    student_name: str
    dish_name: str
    price: int
    quantity: int
    notes: Optional[str] = ""
    is_paid: int
    created_at: str

class TimeWindowStatus(BaseModel):
    is_open: bool
    current_time: str
    target_meal_date: Optional[str] = None
    target_day_name: Optional[str] = None
    restaurant: Optional[RestaurantResponse] = None
    message: str
    time_remaining_seconds: Optional[int] = 0
    is_test_mode: bool = False
