const { createApp } = Vue;

createApp({
  data() {
    return {
      status: {
        is_open: true,
        current_time: '',
        target_meal_date: '',
        target_day_name: '',
        message: '',
        time_remaining_seconds: 0,
        is_test_mode: false
      },
      todayRestaurant: null,
      weeklySchedule: { days: [] },
      todayOrders: {
        total_qty: 0,
        total_amount: 0,
        total_students: 0,
        paid_count: 0,
        dish_summary: [],
        student_orders: [],
        orders: []
      },

      // 多品項導航與篩選
      selectedSubCategory: 'ALL',
      searchQuery: '',

      // 自訂手動輸入菜名工具
      customDish: {
        name: '',
        price: 100
      },

      // 同學個人資訊與購物車點餐清單 (自選訂幾項)
      studentInfo: {
        seat_number: localStorage.getItem('school_lunch_seat') || '',
        student_name: localStorage.getItem('school_lunch_name') || ''
      },
      cartItems: [],
      isSubmitting: false,

      // 實體紙本菜單照片燈箱
      showMenuPhotoModal: false,
      previewImageUrl: '',
      previewTitle: '',

      // 倒數計時
      countdownTimer: null,
      remainingSeconds: 0,

      // 查詢個人點餐紀錄
      showMyOrderModal: false,
      myOrderSeatInput: '',
      isSearchingMyOrder: false,
      myOrderResult: null,

      // 同學回饋彈窗
      showFeedbackModal: false,
      allRestaurants: [],
      quickTags: ['份量超大', '神級美味', 'CP值神級', '吃得飽', '清爽不油', '肉很厚', '送餐準時', '加麵免費', '大胃王必點'],
      feedbackForm: {
        restaurant_id: 1,
        seat_number: '',
        student_name: '',
        rating: 5,
        selectedTags: [],
        comment: ''
      }
    };
  },
  computed: {
    countdownFormatted() {
      if (this.remainingSeconds <= 0) return '00:00:00';
      const hours = Math.floor(this.remainingSeconds / 3600);
      const minutes = Math.floor((this.remainingSeconds % 3600) / 60);
      const seconds = this.remainingSeconds % 60;
      return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    },
    groupedMenu() {
      // 依「系列 + 菜名」合併多個規格（豬肉/牛肉/雞腿肉、麵種、飯量）
      if (!this.todayRestaurant || !this.todayRestaurant.menu_items) return [];
      const map = new Map();
      for (const m of this.todayRestaurant.menu_items) {
        const key = `${m.sub_category}||${m.name}`;
        if (!map.has(key)) {
          map.set(key, { key, name: m.name, sub_category: m.sub_category, description: m.description, variants: [] });
        }
        map.get(key).variants.push({ id: m.id, variant: m.variant || '', price: m.price });
      }
      return [...map.values()].map(g => {
        const prices = g.variants.map(v => v.price);
        const min = Math.min(...prices), max = Math.max(...prices);
        g.samePrice = min === max;
        g.priceLabel = g.samePrice ? `$${min}` : `$${min}~${max}`;
        return g;
      });
    },
    displayedDishes() {
      let list = this.groupedMenu;

      // 1. 系列次分類過濾
      if (this.selectedSubCategory !== 'ALL') {
        list = list.filter(d => d.sub_category === this.selectedSubCategory);
      }

      // 2. 關鍵字搜尋過濾（菜名、說明、系列、規格）
      const q = (this.searchQuery || '').trim().toLowerCase();
      if (q) {
        list = list.filter(d =>
          d.name.toLowerCase().includes(q) ||
          (d.description && d.description.toLowerCase().includes(q)) ||
          (d.sub_category && d.sub_category.toLowerCase().includes(q)) ||
          d.variants.some(v => v.variant.toLowerCase().includes(q))
        );
      }

      return list;
    },
    totalCartQuantity() {
      return this.cartItems.reduce((sum, item) => sum + (item.quantity || 1), 0);
    },
    totalCartAmount() {
      return this.cartItems.reduce((sum, item) => sum + (item.price * (item.quantity || 1)), 0);
    }
  },
  mounted() {
    this.fetchStatus();
    this.fetchTodayOrders();
    this.fetchWeeklySchedule();
    this.fetchAllRestaurants();

    // 倒數計時
    this.countdownTimer = setInterval(() => {
      if (this.remainingSeconds > 0) {
        this.remainingSeconds--;
      }
    }, 1000);

    // 30 秒自動更新
    setInterval(() => {
      this.fetchTodayOrders();
      this.fetchStatus(true);
    }, 30000);
  },
  methods: {
    async fetchStatus(silent = false) {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        this.status = data;
        this.todayRestaurant = data.restaurant;
        this.remainingSeconds = data.time_remaining_seconds || 0;
      } catch (err) {
        if (!silent) console.error('Failed to fetch status:', err);
      }
    },
    async fetchTodayOrders() {
      try {
        const res = await fetch('/api/orders/today');
        const data = await res.json();
        this.todayOrders = data;
      } catch (err) {
        console.error('Failed to fetch orders:', err);
      }
    },
    async fetchWeeklySchedule(weekStart) {
      try {
        const url = weekStart ? `/api/schedule/current-week?week_start=${weekStart}` : '/api/schedule/current-week';
        const res = await fetch(url);
        const data = await res.json();
        this.weeklySchedule = data;
      } catch (err) {
        console.error('Failed to fetch weekly schedule:', err);
      }
    },
    async fetchAllRestaurants() {
      try {
        const res = await fetch('/api/admin/restaurants');
        const data = await res.json();
        this.allRestaurants = data;
        if (data.length > 0) {
          this.feedbackForm.restaurant_id = data[0].id;
        }
      } catch (err) {
        console.error('Failed to fetch all restaurants:', err);
      }
    },

    countSubCategoryItems(subCat) {
      return this.groupedMenu.filter(d => d.sub_category === subCat).length;
    },

    // 購物車點餐操作 (滿足同學自選多品項)
    addToCart(dish, variant) {
      const v = variant || (dish.variants ? dish.variants[0] : { variant: '', price: dish.price });
      const fullName = v.variant ? `${dish.name}（${v.variant}）` : dish.name;
      const existing = this.cartItems.find(item => item.dish_name === fullName && item.price === v.price);
      if (existing) {
        existing.quantity++;
      } else {
        this.cartItems.push({
          dish_name: fullName,
          price: v.price,
          quantity: 1,
          notes: ''
        });
      }
    },

    addCustomDishToCart() {
      if (!this.customDish.name.trim()) {
        alert('請輸入自訂菜名！');
        return;
      }
      this.cartItems.push({
        dish_name: this.customDish.name.trim(),
        price: this.customDish.price || 0,
        quantity: 1,
        notes: ''
      });
      this.customDish.name = '';
    },

    decreaseQty(idx) {
      if (this.cartItems[idx].quantity > 1) {
        this.cartItems[idx].quantity--;
      } else {
        this.removeFromCart(idx);
      }
    },

    removeFromCart(idx) {
      this.cartItems.splice(idx, 1);
    },

    async submitOrderCart() {
      if (!this.status.is_open) {
        alert('目前非訂餐時段，無法送出訂單！請見時段公告。');
        return;
      }
      if (!this.studentInfo.seat_number.trim() || !this.studentInfo.student_name.trim()) {
        alert('請填寫座號與姓名！');
        return;
      }
      if (this.cartItems.length === 0) {
        alert('點餐清單不可為空，請從左側點選餐點加入！');
        return;
      }

      this.isSubmitting = true;
      try {
        const res = await fetch('/api/orders', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            order_date: this.status.target_meal_date || '',
            seat_number: this.studentInfo.seat_number.trim(),
            student_name: this.studentInfo.student_name.trim(),
            items: this.cartItems
          })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '點餐失敗');

        // 記住座號與姓名
        localStorage.setItem('school_lunch_seat', this.studentInfo.seat_number.trim());
        localStorage.setItem('school_lunch_name', this.studentInfo.student_name.trim());

        alert(data.message);

        // 清空點餐清單
        this.cartItems = [];

        // 重新讀取訂單清單
        this.fetchTodayOrders();
      } catch (err) {
        alert(err.message);
      } finally {
        this.isSubmitting = false;
      }
    },

    // 預覽實體菜單照片
    previewSpecificMenuPhoto(url, title) {
      this.previewImageUrl = url;
      this.previewTitle = title;
      this.showMenuPhotoModal = true;
    },

    // 查詢個人點餐紀錄彈窗
    openMyOrderModal() {
      this.myOrderSeatInput = this.studentInfo.seat_number || localStorage.getItem('school_lunch_seat') || '';
      this.showMyOrderModal = true;
      if (this.myOrderSeatInput.trim()) {
        this.searchMyOrders();
      }
    },
    async searchMyOrders() {
      const seat = this.myOrderSeatInput.trim();
      if (!seat) {
        alert('請先輸入座號！');
        return;
      }
      this.isSearchingMyOrder = true;
      try {
        const res = await fetch(`/api/orders/my-history?seat_number=${encodeURIComponent(seat)}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '查詢失敗');
        this.myOrderResult = data;
      } catch (err) {
        alert(err.message);
      } finally {
        this.isSearchingMyOrder = false;
      }
    },

    // 同學回饋彈窗
    openFeedbackModal() {
      this.feedbackForm.seat_number = this.studentInfo.seat_number;
      this.feedbackForm.student_name = this.studentInfo.student_name;
      if (this.todayRestaurant) {
        this.feedbackForm.restaurant_id = this.todayRestaurant.id;
      }
      this.showFeedbackModal = true;
    },
    toggleTag(tag) {
      const idx = this.feedbackForm.selectedTags.indexOf(tag);
      if (idx > -1) {
        this.feedbackForm.selectedTags.splice(idx, 1);
      } else {
        this.feedbackForm.selectedTags.push(tag);
      }
    },
    async submitFeedback() {
      try {
        const res = await fetch('/api/feedbacks', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            restaurant_id: this.feedbackForm.restaurant_id,
            seat_number: this.feedbackForm.seat_number,
            student_name: this.feedbackForm.student_name,
            rating: this.feedbackForm.rating,
            comment: this.feedbackForm.comment,
            tags: this.feedbackForm.selectedTags.join(',')
          })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '提交失敗');

        alert(data.message);
        this.showFeedbackModal = false;
        this.feedbackForm.comment = '';
        this.feedbackForm.selectedTags = [];

        this.fetchStatus();
      } catch (err) {
        alert(err.message);
      }
    }
  }
}).mount('#app');
