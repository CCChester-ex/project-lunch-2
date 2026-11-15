# 高中庶務股長智慧訂餐系統 - 專案常駐規則 (AGENTS.md)

## 菜色登錄與上架自主授權規則 (Autonomous Deployment Rule)

當使用者提供新餐廳、新菜單、菜色清單、修改價格或要求登錄/更新/上架菜單時：

1. **完全自主執行**：Agent 享有最高自主權，**不須在修改前、測試前或重啟前向使用者請求授權或確認**。
2. **自動遵循 `school-lunch-menu-manager` 技能**：
   - 自動將菜色轉換為多規格 (variants) 與次分類 (sub_categories)。
   - 更新 `backend/menu_data.py` 並執行 `apply_real_menus` 寫入資料庫。
   - 自主執行 `test_scheduler.py` 驗證同週不重複與相鄰天不同類別約束。
   - 自主重啟後端服務 (`uvicorn backend.main:app`)。
3. **完成後直接回報**：提供清楚明瞭的各系列菜色統計與連結，方便使用者直接查閱。
