const { createApp } = Vue;

createApp({
  data() {
    return {
      currentTab: 'schedule',
      tabs: [
        { id: 'schedule', name: '智慧排餐規劃', icon: '📅' },
        { id: 'orders', name: '今日叫餐與收費', icon: '📞' },
        { id: 'restaurants', name: '餐廳與菜單管理', icon: '🏬' },
        { id: 'feedbacks', name: '同學回饋評分', icon: '⭐' },
        { id: 'settings', name: '時段與測試設定', icon: '⚙️' }
      ],

      serverTime: '',
      allRestaurants: [],
      scheduleWeekStart: '',
      prevWeekMonday: '',
      nextWeekMonday: '',
      isActiveWeek: true,
      scheduleDays: [],
      scheduleValidation: { valid: true, errors: [], warnings: [] },
      isGenerating: false,
      isSaving: false,

      // 訂單與叫餐 (含同學分組視圖)
      todayOrders: {
        order_date: '',
        total_qty: 0,
        total_amount: 0,
        total_students: 0,
        paid_count: 0,
        dish_summary: [],
        student_orders: [],
        orders: []
      },

      // 同學回饋
      feedbacks: [],

      // 系統設定
      settings: {
        test_mode: true,
        simulated_time: '',
        discord_webhook_url: '',
        discord_enabled: false,
        current_server_time: ''
      },

      // 後台 HMAC 權杖驗證
      adminToken: sessionStorage.getItem('admin_token') || '',
      isAuthenticated: !!sessionStorage.getItem('admin_token'),
      inputPin: '',
      newAdminPin: '',
      pinError: '',
      isCheckingPin: false,
      isTestingDiscord: false,

      // 菜單相片預覽燈箱
      showPhotoModal: false,
      modalPhotoUrl: '',
      modalPhotoTitle: '',

      // 新增餐廳彈窗
      showAddRestaurantModal: false,
      newRestClosedDaysList: [],
      newRest: {
        name: '',
        category: '便當快炒',
        phone: '',
        min_order: 300,
        image_url: '',
        notes: '',
        closed_days: '',
        menu_items: []
      },
      newRestDish: {
        sub_category: '招牌系列',
        name: '',
        price: 85
      },

      // 編輯餐廳分類與公休日彈窗
      showEditClosedModal: false,
      editingRest: null,
      editCategory: '便當快炒',
      editClosedDaysList: [],

      // 新增單一菜色彈窗
      showAddDishModal: false,
      targetRestForDish: null,
      singleDish: {
        sub_category: '熱門推薦',
        name: '',
        price: 85
      }
    };
  },
  computed: {
    paymentProgressPercent() {
      if (this.todayOrders.total_students === 0) return 0;
      return Math.round((this.todayOrders.paid_count / this.todayOrders.total_students) * 100);
    },
    callDraftText() {
      const date = this.todayOrders.order_date || '今日';
      const summary = this.todayOrders.dish_summary || [];
      const scheduledDay = this.scheduleDays.find(d => d.target_date === date);
      const restName = scheduledDay?.restaurant_name || '午餐合作店家';
      const restPhone = scheduledDay?.phone || '無電話';

      let text = `【${restName}】午餐訂餐明細表\n`;
      text += `店家電話：${restPhone}\n`;
      text += `目標餐期：${date} 中午 11:55 送達\n`;
      text += `-------------------------------------------\n`;

      if (summary.length === 0) {
        text += `(目前尚無同學點餐)\n`;
      } else {
        summary.forEach(item => {
          text += `▪ ${item.dish_name}  x ${item.total_qty} 份  (小計: $${item.subtotal})\n`;
        });
      }

      text += `-------------------------------------------\n`;
      text += `總份數：${this.todayOrders.total_qty} 份\n`;
      text += `應付總額：$${this.todayOrders.total_amount} 元\n`;
      text += `送達地點：高三2班 走廊叫餐區\n`;
      text += `訂購人：高三2班 庶務股長 (0912-345-678)\n`;
      return text;
    }
  },
  mounted() {
    if (this.isAuthenticated) {
      this.initAdminData();
    }
  },
  methods: {
    async adminFetch(url, options = {}) {
      const headers = Object.assign({}, options.headers || {});
      if (this.adminToken) {
        headers['Authorization'] = `Bearer ${this.adminToken}`;
      }
      const res = await fetch(url, Object.assign({}, options, { headers }));
      if (res.status === 401) {
        this.logout();
        this.pinError = '登入時效已過期或未授權，請重新輸入管理員密碼！';
      }
      return res;
    },
    async verifyPin() {
      this.isCheckingPin = true;
      this.pinError = '';
      try {
        const res = await fetch('/api/admin/verify-pin', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pin: this.inputPin })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '密碼錯誤！');

        this.adminToken = data.token;
        this.isAuthenticated = true;
        sessionStorage.setItem('admin_token', data.token);
        sessionStorage.removeItem('admin_authenticated');
        this.inputPin = '';
        this.initAdminData();
      } catch (err) {
        this.pinError = err.message || '密碼錯誤，請重新輸入！';
        this.inputPin = '';
      } finally {
        this.isCheckingPin = false;
      }
    },
    logout() {
      this.adminToken = '';
      this.isAuthenticated = false;
      sessionStorage.removeItem('admin_token');
      sessionStorage.removeItem('admin_authenticated');
      this.inputPin = '';
      this.pinError = '';
    },
    initAdminData() {
      this.fetchSettings();
      this.fetchRestaurants().then(() => {
        this.fetchCurrentSchedule();
      });
      this.fetchTodayOrders();
      this.fetchFeedbacks();

      setInterval(() => {
        if (this.isAuthenticated) {
          this.fetchSettings(true);
        }
      }, 10000);
    },
    async testDiscordWebhook() {
      if (!this.settings.discord_webhook_url) {
        alert('請先輸入 Discord Webhook 網址！');
        return;
      }
      this.isTestingDiscord = true;
      try {
        const res = await this.adminFetch('/api/admin/discord/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ webhook_url: this.settings.discord_webhook_url })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '發送失敗');
        alert(data.message || '✓ Discord 測試訊息發送成功！');
      } catch (err) {
        alert(err.message);
      } finally {
        this.isTestingDiscord = false;
      }
    },
    async saveDiscordSettings() {
      try {
        const res = await this.adminFetch('/api/admin/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            discord_webhook_url: this.settings.discord_webhook_url,
            discord_enabled: this.settings.discord_enabled
          })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '儲存失敗');
        alert('✓ Discord 班群提醒設定已儲存！');
      } catch (err) {
        alert('儲存失敗: ' + err.message);
      }
    },
    async changeAdminPin() {
      if (!this.newAdminPin || this.newAdminPin.length < 6) {
        alert('新密碼請至少輸入 6 碼以上！');
        return;
      }
      try {
        const res = await this.adminFetch('/api/admin/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ new_admin_pin: this.newAdminPin })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '更新密碼失敗');
        this.newAdminPin = '';
        alert('✓ 後台管理員密碼已成功更新！下次登入請使用新密碼。');
      } catch (err) {
        alert(err.message);
      }
    },
    async fetchSettings(silent = false) {
      try {
        const res = await this.adminFetch('/api/admin/settings');
        if (!res.ok) return;
        const data = await res.json();
        this.settings = data;
        this.serverTime = data.current_server_time;
      } catch (err) {
        if (!silent) console.error(err);
      }
    },
    async fetchRestaurants() {
      try {
        const res = await this.adminFetch('/api/admin/restaurants');
        if (!res.ok) return;
        const data = await res.json();
        this.allRestaurants = data;
      } catch (err) {
        console.error('Failed to fetch restaurants:', err);
      }
    },
    async fetchCurrentSchedule(weekStart) {
      try {
        const url = weekStart ? `/api/schedule/current-week?week_start=${weekStart}` : '/api/schedule/current-week';
        const res = await fetch(url);
        const data = await res.json();
        this.scheduleWeekStart = data.week_start_date;
        this.prevWeekMonday = data.prev_week_monday;
        this.nextWeekMonday = data.next_week_monday;
        this.isActiveWeek = data.is_active_week;
        this.scheduleDays = data.days;
        this.validateScheduleLocal();
      } catch (err) {
        console.error('Failed to fetch schedule:', err);
      }
    },
    async fetchTodayOrders() {
      try {
        const res = await fetch('/api/orders/today');
        const data = await res.json();
        this.todayOrders = data;
      } catch (err) {
        console.error('Failed to fetch today orders:', err);
      }
    },
    async fetchFeedbacks() {
      try {
        const res = await this.adminFetch('/api/admin/feedbacks');
        if (!res.ok) return;
        const data = await res.json();
        this.feedbacks = data;
      } catch (err) {
        console.error('Failed to fetch feedbacks:', err);
      }
    },

    // 智慧自動排餐
    async triggerAutoPlan() {
      this.isGenerating = true;
      try {
        const res = await this.adminFetch('/api/admin/schedule/auto-plan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ week_start_date: this.scheduleWeekStart })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '自動排餐失敗');

        data.plan.forEach((p, idx) => {
          if (this.scheduleDays[idx]) {
            this.scheduleDays[idx].restaurant_id = p.restaurant_id;
            this.scheduleDays[idx].restaurant_name = p.restaurant_name;
            this.scheduleDays[idx].category = p.category;
            this.scheduleDays[idx].phone = p.phone;
            this.scheduleDays[idx].avg_rating = p.avg_rating;
            this.scheduleDays[idx].notes = p.notes;
            this.scheduleDays[idx].is_manual_override = 0;
            const fullRest = this.allRestaurants.find(r => r.id === p.restaurant_id);
            if (fullRest) {
              this.scheduleDays[idx].image_url = fullRest.image_url;
            }
          }
        });

        this.validateScheduleLocal();
        alert('⚡ 智慧自動排餐完成！已嚴格滿足「每週同店不重複」與「相鄰天不同類型」之限制條件。');
      } catch (err) {
        alert(err.message);
      } finally {
        this.isGenerating = false;
      }
    },

    onRestaurantSelect(day) {
      const rest = this.allRestaurants.find(r => r.id === day.restaurant_id);
      if (rest) {
        day.restaurant_name = rest.name;
        day.category = rest.category;
        day.phone = rest.phone;
        day.image_url = rest.image_url;
        day.avg_rating = rest.avg_rating;
        day.notes = '股長手動指定';
        day.is_manual_override = 1;
      }
      this.validateScheduleLocal();
    },

    validateScheduleLocal() {
      const errors = [];
      const seenIds = {};
      let prevCat = null;
      let prevDayName = null;

      for (const day of this.scheduleDays) {
        if (!day.restaurant_id) {
          errors.push(`${day.day_name} 尚未指定店家！`);
          continue;
        }

        if (seenIds[day.restaurant_id]) {
          errors.push(`【每週同店不可重複】店家「${day.restaurant_name}」同時出現在 ${seenIds[day.restaurant_id]} 與 ${day.day_name}！`);
        } else {
          seenIds[day.restaurant_id] = day.day_name;
        }

        if (prevCat && day.category && prevCat.trim() === day.category.trim()) {
          errors.push(`【同類型不連續出現】${prevDayName} 與 ${day.day_name} 連續兩天皆為「${day.category}」店家！`);
        }

        prevCat = day.category;
        prevDayName = day.day_name;
      }

      this.scheduleValidation = {
        valid: errors.length === 0,
        errors: errors
      };
    },

    hasConflictForDay(dow) {
      if (this.scheduleValidation.valid) return false;
      const day = this.scheduleDays.find(d => d.day_of_week === dow);
      if (!day) return false;
      return this.scheduleValidation.errors.some(err => err.includes(day.day_name));
    },

    async saveSchedule() {
      this.validateScheduleLocal();
      if (!this.scheduleValidation.valid) {
        alert('排程違反約束條件，無法儲存！請參考警示紅框修改。');
        return;
      }

      this.isSaving = true;
      try {
        const res = await this.adminFetch('/api/admin/schedule/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            week_start_date: this.scheduleWeekStart,
            days: this.scheduleDays
          })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '儲存排餐失敗');

        alert('✓ 本週排餐已成功儲存！前台點餐已即時同步更新。');
      } catch (err) {
        alert(err.message);
      } finally {
        this.isSaving = false;
      }
    },

    copyCallDraft() {
      navigator.clipboard.writeText(this.callDraftText).then(() => {
        alert('已複製叫餐草稿至剪貼簿！可直接貼至 LINE 或照稿撥打電話。');
      }).catch(() => {
        alert('複製失敗，請手動複製下方文字方塊。');
      });
    },

    // 一人多品項整單收費切換
    async toggleStudentPayment(student) {
      const nextPaid = student.is_paid === 1 ? 0 : 1;
      try {
        const res = await this.adminFetch('/api/admin/orders/student-payment', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            seat_number: student.seat_number,
            order_date: this.todayOrders.order_date,
            is_paid: nextPaid
          })
        });
        if (res.ok) {
          student.is_paid = nextPaid;
          student.items.forEach(it => it.is_paid = nextPaid);
          this.todayOrders.paid_count = this.todayOrders.student_orders.filter(s => s.is_paid === 1).length;
        }
      } catch (err) {
        console.error(err);
      }
    },

    async batchSetPayment(paid) {
      if (!confirm(`確定要將今日所有同學皆設為【${paid ? '已收費' : '未收費'}】嗎？`)) return;
      try {
        const res = await this.adminFetch('/api/admin/orders/batch-payment', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            date: this.todayOrders.order_date,
            is_paid: paid ? 1 : 0
          })
        });
        const data = await res.json();
        if (res.ok) {
          this.todayOrders.student_orders.forEach(s => {
            s.is_paid = paid ? 1 : 0;
            s.items.forEach(it => it.is_paid = paid ? 1 : 0);
          });
          this.todayOrders.paid_count = paid ? this.todayOrders.student_orders.length : 0;
          alert(data.message);
        }
      } catch (err) {
        alert(err.message);
      }
    },

    async deleteStudentOrders(student) {
      if (!confirm(`確定要取消座號 ${student.seat_number} 號 ${student.student_name} 的全部點餐嗎？`)) return;
      try {
        for (const orderId of student.order_ids) {
          await this.adminFetch(`/api/admin/orders/${orderId}`, { method: 'DELETE' });
        }
        this.fetchTodayOrders();
      } catch (err) {
        alert(err.message);
      }
    },

    previewMenuPhoto(url, title) {
      this.modalPhotoUrl = url;
      this.modalPhotoTitle = title;
      this.showPhotoModal = true;
    },

    // 餐廳管理功能
    addDishToNewRest() {
      if (!this.newRestDish.name) return;
      this.newRest.menu_items.push({
        sub_category: this.newRestDish.sub_category || '主要系列',
        name: this.newRestDish.name,
        price: this.newRestDish.price || 85,
        description: ''
      });
      this.newRestDish.name = '';
      this.newRestDish.price = 85;
    },

    async createRestaurant() {
      try {
        this.newRest.closed_days = this.newRestClosedDaysList.sort().join(',');
        const res = await this.adminFetch('/api/admin/restaurants', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.newRest)
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '新增失敗');

        alert(data.message);
        this.showAddRestaurantModal = false;
        this.newRest = { name: '', category: '便當快炒', phone: '', min_order: 300, image_url: '', notes: '', closed_days: '', menu_items: [] };
        this.newRestClosedDaysList = [];
        this.fetchRestaurants();
      } catch (err) {
        alert(err.message);
      }
    },

    async deleteRestaurant(restId) {
      if (!confirm('確定要刪除此餐廳嗎？')) return;
      try {
        const res = await this.adminFetch(`/api/admin/restaurants/${restId}`, { method: 'DELETE' });
        if (res.ok) {
          this.fetchRestaurants();
          this.fetchCurrentSchedule();
        }
      } catch (err) {
        alert(err.message);
      }
    },

    openAddDishModal(rest) {
      this.targetRestForDish = rest;
      this.singleDish = { sub_category: '熱門推薦', name: '', price: 85 };
      this.showAddDishModal = true;
    },

    async submitAddSingleDish() {
      if (!this.targetRestForDish) return;
      try {
        const res = await this.adminFetch(`/api/admin/restaurants/${this.targetRestForDish.id}/menu`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.singleDish)
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '新增失敗');

        alert(data.message);
        this.showAddDishModal = false;
        this.fetchRestaurants();
      } catch (err) {
        alert(err.message);
      }
    },

    async deleteMenuItem(itemId) {
      if (!confirm('確定要刪除這道菜嗎？')) return;
      try {
        const res = await this.adminFetch(`/api/admin/menu/${itemId}`, { method: 'DELETE' });
        if (res.ok) {
          this.fetchRestaurants();
        }
      } catch (err) {
        alert(err.message);
      }
    },

    // 公休日格式化與彈窗編輯
    formatClosedDays(raw) {
      if (!raw || !String(raw).trim()) return '無公休 (週一至週日皆可營業)';
      const dayNames = { '1': '週一', '2': '週二', '3': '週三', '4': '週四', '5': '週五', '6': '週六', '7': '週日' };
      const parts = String(raw).replace(/;/g, ',').split(',').map(s => s.trim()).filter(Boolean);
      const names = parts.map(p => dayNames[p] || `週${p}`);
      return names.length > 0 ? names.join('、') + ' 公休' : '無';
    },

    openEditClosedDaysModal(rest) {
      this.editingRest = rest;
      this.editCategory = rest.category || '便當快炒';
      const raw = rest.closed_days || '';
      this.editClosedDaysList = String(raw).replace(/;/g, ',').split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n));
      this.showEditClosedModal = true;
    },

    async saveRestaurantClosedDays() {
      if (!this.editingRest) return;
      const closedStr = this.editClosedDaysList.sort().join(',');
      try {
        const res = await this.adminFetch(`/api/admin/restaurants/${this.editingRest.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: this.editingRest.name,
            category: this.editCategory,
            phone: this.editingRest.phone || '',
            min_order: this.editingRest.min_order || 0,
            notes: this.editingRest.notes || '',
            image_url: this.editingRest.image_url || '',
            closed_days: closedStr,
            is_active: this.editingRest.is_active ?? 1
          })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '更新失敗');

        this.editingRest.category = this.editCategory;
        this.editingRest.closed_days = closedStr;
        this.showEditClosedModal = false;
        alert(`已成功更新【${this.editingRest.name}】分類為「${this.editCategory}」、公休日為：${this.formatClosedDays(closedStr)}`);
        this.fetchRestaurants();
        this.fetchCurrentSchedule();
      } catch (err) {
        alert(err.message);
      }
    },

    // 系統與時段測試設定
    async saveSettings() {
      try {
        await this.adminFetch('/api/admin/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.settings)
        });
      } catch (err) {
        console.error(err);
      }
    },

    async setSimulatedTimePreset(preset) {
      let isoString = '';
      if (preset === 'sun_evening') {
        isoString = '2026-11-08T18:00:00';
      } else if (preset === 'mon_morning') {
        isoString = '2026-11-09T09:00:00';
      } else if (preset === 'mon_noon') {
        isoString = '2026-11-09T11:30:00';
      } else if (preset === 'fri_night') {
        isoString = '2026-11-13T20:00:00';
      }

      this.settings.simulated_time = isoString;
      await this.saveSettings();
      alert(`已將系統時間模擬為：${isoString}`);
      this.fetchSettings();
    },

    async clearSimulatedTime() {
      this.settings.simulated_time = '';
      await this.saveSettings();
      alert('已恢復伺服器真實時間！');
      this.fetchSettings();
    }
  }
}).mount('#app');
