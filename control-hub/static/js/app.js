// Lure - Mission Control Reactive Controller (Alpine.js)
// Apple Studio Light & Bento Grid Architecture

// Web Audio API Synthesizer (Zero Dependencies, Offline Apple Chimes)
class SoundFx {
  constructor() {
    this.ctx = null;
  }

  ensureContext() {
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) this.ctx = new AudioCtx();
    }
    if (this.ctx && this.ctx.state === 'suspended') {
      this.ctx.resume();
    }
  }

  playChime(type = 'success') {
    try {
      this.ensureContext();
      if (!this.ctx) return;

      const now = this.ctx.currentTime;

      if (type === 'success') {
        // Apple-like clean major arpeggio: C6 (1046.5Hz) -> E6 (1318.5Hz) -> G6 (1568Hz)
        const notes = [1046.5, 1318.5, 1568.0];
        notes.forEach((freq, idx) => {
          const osc = this.ctx.createOscillator();
          const gain = this.ctx.createGain();

          osc.type = 'sine';
          osc.frequency.setValueAtTime(freq, now + idx * 0.08);

          gain.gain.setValueAtTime(0, now + idx * 0.08);
          gain.gain.linearRampToValueAtTime(0.22, now + idx * 0.08 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.001, now + idx * 0.08 + 0.45);

          osc.connect(gain);
          gain.connect(this.ctx.destination);

          osc.start(now + idx * 0.08);
          osc.stop(now + idx * 0.08 + 0.5);
        });
      } else if (type === 'cooldown_done') {
        // Soft pristine bell: A5 (880Hz)
        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();

        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, now);

        gain.gain.setValueAtTime(0, now);
        gain.gain.linearRampToValueAtTime(0.2, now + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.6);

        osc.connect(gain);
        gain.connect(this.ctx.destination);

        osc.start(now);
        osc.stop(now + 0.65);
      } else if (type === 'warning') {
        // Double warning tone
        [587.33, 440].forEach((freq, idx) => {
          const osc = this.ctx.createOscillator();
          const gain = this.ctx.createGain();

          osc.type = 'triangle';
          osc.frequency.setValueAtTime(freq, now + idx * 0.12);

          gain.gain.setValueAtTime(0.18, now + idx * 0.12);
          gain.gain.exponentialRampToValueAtTime(0.001, now + idx * 0.12 + 0.3);

          osc.connect(gain);
          gain.connect(this.ctx.destination);

          osc.start(now + idx * 0.12);
          osc.stop(now + idx * 0.12 + 0.35);
        });
      }
    } catch (e) {
      console.warn('Audio play error:', e);
    }
  }
}

const sfx = new SoundFx();

function registerHubApp() {
  if (!window.Alpine) return;

  Alpine.data('hubApp', () => ({
    currentTab: 'dashboard',
    soundEnabled: localStorage.getItem('lure_sound_enabled') !== 'false',
    expandedAccount: null,
    isScraping: false,
    isScrapingGmaps: false,
    scrapeModalOpen: false,
    scrapeMode: 'meta',
    scrapeOptions: { niches: [], geos: [], presets: [] },
    scrapeForm: { niches: [], geos: [], customText: '', max_ads: 10, queries_per_pair: 8, allow_websites: false },
    scrapePreview: null,
    scrapePreviewError: '',
    mockupLeadId: null,
    emailModalOpen: false,
    emailOptions: { counts: { pending: 0, pending_by_source: {}, sent: 0, sent_24h: 0 }, sender: '', configured: true, max_per_run: 100, delay_min: 20, delay_max: 45, recommended_daily: 50 },
    emailForm: { count: 10, source: 'all', niche: 'all', delay_min: 20, delay_max: 45, testTo: '' },
    emailPreview: null,
    emailPreviewError: '',
    emailTemplate: { open: false, subjects: '', body: '', saving: false },
    emailTesting: false,
    setupOpen: false,
    setupStep: 0,
    setupBusy: false,
    setupError: '',
    setup: {
      completed: true,
      chrome: { ok: true, path: '' },
      email: { ok: true, sender: '', sender_name: '' },
      logins: { fb: { ok: true, profile_exists: false }, ig: { ok: true, profile_exists: false } },
      whatsapp: { ok: true, api_url: '', profile_1: '', profile_2: '' }
    },
    setupForm: { sender: '', app_password: '', sender_name: '', wa_api_url: '', wa_profile_1: '', wa_profile_2: '' },
    setupSteps: [
      { id: 'chrome', title: 'Браузер' },
      { id: 'email', title: 'Почта' },
      { id: 'accounts', title: 'Аккаунты' },
      { id: 'whatsapp', title: 'WhatsApp' }
    ],
    metrics: {
      fb: { name: 'Facebook', sent_today: 0, daily_limit: 10, ready_leads: 0, percentage: 0, color: '#0066CC', available_slots: 10, next_slot_in: null, next_slot_time: null },
      ig: { name: 'Instagram', sent_today: 0, daily_limit: 10, ready_leads: 0, percentage: 0, color: '#E1306C', available_slots: 10, next_slot_in: null, next_slot_time: null },
      wa1: { name: 'WhatsApp #1', sent_today: 0, daily_limit: 6, ready_leads: 0, percentage: 0, color: '#16A34A', available_slots: 6, next_slot_in: null, next_slot_time: null },
      wa2: { name: 'WhatsApp #2', sent_today: 0, daily_limit: 6, ready_leads: 0, percentage: 0, color: '#0D9488', available_slots: 6, next_slot_in: null, next_slot_time: null },
      total_sent_today: 0,
      total_limit_today: 32,
      total_ready: 0,
      email: { total: 0, sent: 0, pending: 0 },
      is_running: false
    },
    status: {
      is_running: false,
      active_channels: [],
      current_activity: 'Система готова к работе',
      email: { running: false, status_text: 'Готов к запуску', sent: 0, failed: 0, total: 0, current: null, pause_until: null },
      channels: {
        fb: { running: false, status_text: 'Готов к запуску', step: 'idle', current_lead: null, cooldown_remaining: 0, cooldown_total: 0 },
        ig: { running: false, status_text: 'Готов к запуску', step: 'idle', current_lead: null, cooldown_remaining: 0, cooldown_total: 0 },
        wa1: { running: false, status_text: 'Готов к запуску', step: 'idle', current_lead: null, cooldown_remaining: 0, cooldown_total: 0 },
        wa2: { running: false, status_text: 'Готов к запуску', step: 'idle', current_lead: null, cooldown_remaining: 0, cooldown_total: 0 }
      }
    },
    leads: [],
    leadFilters: {
      platform: 'ALL',
      status: 'ALL',
      search: '',
      hideDisqualified: true
    },
    logs: [],
    miniLogs: [],
    logFilter: 'ALL',
    wsConnected: false,
    autoScrollLogs: true,
    actionLoading: false,
    toasts: [],
    replies: [],
    replyWatcher: { enabled: true, interval: 180, checking: false, channels: {} },
    repliesSeenAt: localStorage.getItem('lure_replies_seen_at') || '',

    get unseenReplies() {
      return this.replies.filter(r => String(r.replied_at) > this.repliesSeenAt).length;
    },

    async fetchReplies() {
      try {
        const res = await fetch('/api/replies');
        if (res.ok) {
          const data = await res.json();
          this.replies = data.items;
          this.replyWatcher = data.watcher;
        }
      } catch (e) {
        console.error('Failed to fetch replies:', e);
      }
    },

    openReplies() {
      this.currentTab = 'replies';
      this.fetchReplies();
    },

    markRepliesSeen() {
      const latest = this.replies.reduce((m, r) => (String(r.replied_at) > m ? String(r.replied_at) : m), '');
      if (latest) {
        this.repliesSeenAt = latest;
        try { localStorage.setItem('lure_replies_seen_at', latest); } catch (e) {}
      }
    },

    async checkRepliesNow() {
      await fetch('/api/replies/check', { method: 'POST' });
      this.showToast('Проверка ответов', 'Заглядываю в открытые браузеры...', 'info');
      setTimeout(() => this.fetchReplies(), 15000);
    },

    async toggleReplyWatcher() {
      const res = await fetch('/api/replies/watcher', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !this.replyWatcher.enabled })
      });
      if (res.ok) this.replyWatcher = await res.json();
    },

    replyChannelName(ch) {
      return { fb: 'Facebook', ig: 'Instagram', wa1: 'WhatsApp #1', wa2: 'WhatsApp #2' }[ch] || ch;
    },

    formatReplyTime(iso) {
      const d = new Date(iso);
      return isNaN(d) ? '' : d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    },

    waNiches: { current: 'all', niches: [] },

    async fetchWaNiches() {
      try {
        this.waNiches = await (await fetch('/api/wa/niches')).json();
      } catch (e) {}
    },

    async setWaNiche(niche) {
      try {
        const res = await fetch('/api/wa/niche', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ niche })
        });
        if (!res.ok) throw new Error();
        this.waNiches.current = niche;
        this.showToast('Ниша рассылки', 'Выбор сохранён, действует со следующего запуска', 'info');
        this.fetchMetrics();
      } catch (e) {
        this.showToast('Не сохранено', 'Не удалось сменить нишу WhatsApp', 'warning');
        this.fetchWaNiches();
      }
    },

    init() {
      console.log('Lure v2.0 Studio Light - Controller Initialized');
      this.fetchWaNiches();
      setInterval(() => this.fetchWaNiches(), 15000);
      this.fetchMetrics();
      this.fetchStatus();
      this.fetchLeads();
      this.fetchReplies();
      this.loadSetup(true);
      this.initWebSocket();

      // Poll metrics & status every 5 seconds
      setInterval(() => {
        this.fetchMetrics();
      }, 5000);
      setInterval(() => this.fetchReplies(), 15000);
    },

    toggleSound() {
      this.soundEnabled = !this.soundEnabled;
      localStorage.setItem('lure_sound_enabled', this.soundEnabled);
      if (this.soundEnabled) {
        sfx.playChime('cooldown_done');
        this.showToast('Звуковые оповещения', 'Звуковые сигналы включены', 'info');
      } else {
        this.showToast('Звуковые оповещения', 'Звук отключен', 'warning');
      }
    },

    // Звуков всего два: ответ лида (приятный) и остановка рассылки (низкий, нисходящий)
    triggerSound(type) {
      if (this.soundEnabled) {
        sfx.playChime(type);
      }
    },

    // Остановку рассылки может сообщить сразу несколько событий (кнопка, процесс, почта): играем один раз
    playStopSound() {
      const now = Date.now();
      if (now - (this.lastStopSoundAt || 0) < 4000) return;
      this.lastStopSoundAt = now;
      this.triggerSound('warning');
    },

    async markReplyHandled(r) {
      try {
        const res = await fetch('/api/replies/handled', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source: r.source, lead_id: r.id })
        });
        const data = await res.json();
        if (data.success) this.replies = this.replies.filter(x => !(x.source === r.source && x.id === r.id));
        else this.showToast('Не удалось', 'Ответ не отмечен как отработанный', 'warning');
      } catch (e) {
        this.showToast('Ошибка сети', 'Не удалось связаться с сервером', 'warning');
      }
    },

    showToast(title, message, type = 'info') {
      const id = Date.now() + Math.random();
      this.toasts.push({ id, title, message, type });
      setTimeout(() => {
        this.toasts = this.toasts.filter(t => t.id !== id);
      }, 5000);
    },

    openAccountInspector(channel) {
      this.expandedAccount = channel;
      this.$nextTick(() => {
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
          window.lucide.createIcons();
        }
      });
    },

    closeAccountInspector() {
      this.expandedAccount = null;
      this.$nextTick(() => {
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
          window.lucide.createIcons();
        }
      });
    },

    async fetchMetrics() {
      try {
        const res = await fetch('/api/metrics');
        if (res.ok) {
          const data = await res.json();
          this.metrics = data;
          if (data.status) {
            this.status = data.status;
          }
        }
      } catch (e) {
        console.error('Failed to fetch metrics:', e);
      }
    },

    async fetchStatus() {
      try {
        const res = await fetch('/api/status');
        if (res.ok) {
          this.status = await res.json();
        }
      } catch (e) {
        console.error('Failed to fetch status:', e);
      }
    },

    async fetchLeads() {
      try {
        let url = `/api/leads?platform=${this.leadFilters.platform}&status=${this.leadFilters.status}`;
        if (this.leadFilters.hideDisqualified) {
          url += '&hide_disqualified=true';
        }
        if (this.leadFilters.search) {
          url += `&q=${encodeURIComponent(this.leadFilters.search)}`;
        }
        const res = await fetch(url);
        if (res.ok) {
          this.leads = await res.json();
        }
      } catch (e) {
        console.error('Failed to fetch leads:', e);
      }
    },

    initWebSocket() {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws/logs`;
      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        this.wsConnected = true;
      };

      ws.onmessage = (event) => {
        try {
          const entry = JSON.parse(event.data);

          // 1. Cooldown Ticker (Updates UI progress bar, NEVER enters logs)
          if (entry.type === 'cooldown_tick') {
            const ch = (entry.data.channel || '').toLowerCase();
            if (this.status.channels[ch]) {
              this.status.channels[ch].cooldown_remaining = entry.data.remaining;
              this.status.channels[ch].cooldown_total = entry.data.total;
              this.status.channels[ch].status_text = `Защита: ${entry.data.formatted}`;
              this.status.channels[ch].step = 'cooldown';
            }
            return;
          }

          // 2. Status Snapshot (Updates full state, NEVER enters logs)
          if (entry.type === 'status_update') {
            if (entry.data) {
              this.status = entry.data;
            }
            return;
          }

          // 3. Message Sent Successfully!
          if (entry.type === 'message_sent') {
            const leadName = entry.data && entry.data.lead_name ? entry.data.lead_name : 'Клиент';
            this.showToast('Сообщение отправлено!', `${entry.channel}: ${leadName}`, 'success');
            this.fetchMetrics();
            this.fetchLeads();
          } else if (entry.type === 'reply_received') {
            this.triggerSound('success');
            this.showToast('Новый ответ!', entry.message, 'success');
            this.fetchReplies();
            this.fetchLeads();
          } else if (entry.type === 'cooldown_done') {
            this.showToast('Защита снята', `Пауза завершена для ${entry.channel}, продолжаю работу`, 'info');
            this.fetchStatus();
          } else if (entry.type === 'email_sent') {
            this.fetchMetrics();
          } else if (entry.type === 'email_finished') {
            const d = entry.data || {};
            if (!d.quiet) {
              this.playStopSound();
              this.showToast(d.mode === 'followup' ? 'Письмо 2' : 'Рассылка на почту', `Отправлено: ${d.sent || 0}, ошибок: ${d.failed || 0}`, d.ok ? 'success' : 'warning');
            }
            this.fetchMetrics();
            if (this.emailModalOpen) this.loadEmailModal(false);
          } else if (entry.type === 'session_finished') {
            // Сбор лидов рассылкой не считается, звук только когда остановилась отправка сообщений
            if (!(entry.message || '').startsWith('Поиск лидов')) this.playStopSound();
            if ((entry.message || '').includes('Google Maps')) this.isScrapingGmaps = false;
            else this.isScraping = false;
            this.showToast('Сессия завершена', `Процесс ${entry.channel} успешно завершен`, 'info');
            this.fetchMetrics();
            this.fetchLeads();
          }

          // 4. Real Log Messages Filtering (Strip any noise, avoid repeats)
          if (entry.message && entry.message.trim()) {
            const cleanText = entry.message.trim();

            if (cleanText.includes('Поиск в Ad Library') || cleanText.includes('Meta Ads Scraper')) {
              this.isScraping = true;
            }

            // Prevent repetitive consecutive identical lines
            const lastMini = this.miniLogs[this.miniLogs.length - 1];
            if (lastMini && lastMini.channel === entry.channel && lastMini.message === cleanText) {
              return;
            }

            const logItem = {
              timestamp: entry.timestamp,
              channel: entry.channel,
              message: cleanText,
              level: entry.level || 'info'
            };

            this.logs.push(logItem);
            if (this.logs.length > 500) this.logs.shift();

            this.miniLogs.push(logItem);
            if (this.miniLogs.length > 12) this.miniLogs.shift();

            if (this.autoScrollLogs) {
              this.$nextTick(() => {
                const el = document.getElementById('logContainer');
                if (el) el.scrollTop = el.scrollHeight;
                const miniEl = document.getElementById('dashboardMiniLogs');
                if (miniEl) miniEl.scrollTop = miniEl.scrollHeight;
              });
            }
          }
        } catch (err) {}
      };

      ws.onclose = () => {
        this.wsConnected = false;
        setTimeout(() => this.initWebSocket(), 3000);
      };
    },

    async triggerAction(target, options = null) {
      this.actionLoading = true;
      if (target === 'meta_scrape') this.isScraping = true;
      if (target === 'gmaps_scrape') this.isScrapingGmaps = true;
      try {
        const res = await fetch('/api/action/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target, options })
        });
        if (res.status === 400) {
          const err = await res.json().catch(() => ({}));
          this.showToast('Нужна настройка', err.detail || 'Сначала завершите настройку Lure', 'warning');
          if (target === 'meta_scrape') this.isScraping = false;
          if (target === 'gmaps_scrape') this.isScrapingGmaps = false;
          this.openSetup();
          return;
        }
        const data = await res.json();
        if (data.status === 'rejected') {
          this.showToast('Не запущено', 'Процесс уже работает или параметры некорректны, подробности в журнале', 'warning');
          if (target === 'meta_scrape') this.isScraping = false;
          if (target === 'gmaps_scrape') this.isScrapingGmaps = false;
          return;
        }
        let desc =`Фоновый процесс ${target.toUpperCase()}`;
        if (target === 'meta_scrape') desc = 'Поиск лидов в Meta Ads Library';
        if (target === 'gmaps_scrape') desc = 'Поиск лидов в Google Maps';
        this.showToast('Действие запущено', `${desc} активирован`, 'info');
        await this.fetchStatus();
        await this.fetchMetrics();
      } catch (e) {
        console.error(e);
        this.showToast('Ошибка запуска', e.message, 'error');
        if (target === 'gmaps_scrape') this.isScrapingGmaps = false; else this.isScraping = false;
      } finally {
        this.actionLoading = false;
      }
    },

    async openScrapeModal(mode = 'meta') {
      this.scrapeMode = mode;
      try {
        const res = await fetch('/api/scrape/options');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        this.scrapeOptions = { niches: data.niches, geos: data.geos, presets: data.presets || [] };
        const sel = data.selection || {};
        this.scrapeForm = {
          niches: (sel.niches || []).filter(id => data.niches.some(n => n.id === id)),
          geos: (sel.geos || []).filter(id => data.geos.some(g => g.id === id)),
          customText: (sel.custom_keywords || []).join(', '),
          max_ads: mode === 'gmaps' ? 30 : (sel.max_ads || 10),
          queries_per_pair: sel.queries_per_pair || 8,
          allow_websites: !!sel.allow_websites
        };
        this.scrapeModalOpen = true;
        await this.refreshScrapePreview();
      } catch (e) {
        console.error(e);
        this.showToast('Ошибка', 'Не удалось загрузить каталог ниш и гео', 'warning');
      }
    },

    applyScrapePreset(preset) {
      this.scrapeForm.niches = [...preset.niches];
      this.scrapeForm.geos = [...preset.geos];
      this.refreshScrapePreview();
    },

    toggleScrapeItem(kind, id) {
      const list = this.scrapeForm[kind];
      const idx = list.indexOf(id);
      if (idx >= 0) list.splice(idx, 1); else list.push(id);
      this.refreshScrapePreview();
    },

    scrapePayload() {
      return {
        niches: this.scrapeForm.niches,
        geos: this.scrapeForm.geos,
        custom_keywords: this.scrapeForm.customText.split(',').map(s => s.trim()).filter(Boolean),
        max_ads: Number(this.scrapeForm.max_ads) || null,
        queries_per_pair: Number(this.scrapeForm.queries_per_pair) || null,
        allow_websites: !!this.scrapeForm.allow_websites
      };
    },

    async refreshScrapePreview() {
      const payload = this.scrapePayload();
      this.scrapePreviewError = '';
      if ((!payload.niches.length && !payload.custom_keywords.length) || !payload.geos.length) {
        this.scrapePreview = null;
        return;
      }
      try {
        const res = await fetch('/api/scrape/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          this.scrapePreview = await res.json();
        } else {
          const err = await res.json().catch(() => ({}));
          this.scrapePreview = null;
          this.scrapePreviewError = err.detail || 'Не удалось посчитать запросы';
        }
      } catch (e) {
        this.scrapePreview = null;
        this.scrapePreviewError = 'Нет связи с сервером';
      }
    },

    async startScrape() {
      if (!this.scrapePreview) return;
      const options = this.scrapePayload();
      this.scrapeModalOpen = false;
      await this.triggerAction(this.scrapeMode === 'gmaps' ? 'gmaps_scrape' : 'meta_scrape', options);
    },

    async openEmailModal() {
      this.emailModalOpen = true;
      await this.loadEmailModal(true);
    },

    async loadEmailModal(resetForm) {
      try {
        const res = await fetch('/api/email/options');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        this.emailOptions = await res.json();
        if (resetForm) {
          const o = this.emailOptions;
          const left = Math.max(0, o.recommended_daily - o.counts.sent_24h);
          this.emailForm.count = Math.max(1, Math.min(o.counts.pending, left || o.recommended_daily));
          this.emailForm.delay_min = o.delay_min;
          this.emailForm.delay_max = o.delay_max;
          try { this.emailForm.testTo = localStorage.getItem('lure_email_test_to') || ''; } catch (e) {}
        }
        await this.refreshEmailPreview();
      } catch (e) {
        console.error(e);
        this.showToast('Ошибка', 'Не удалось загрузить данные почтовой рассылки', 'warning');
      }
    },

    emailCount() {
      const n = Math.floor(Number(this.emailForm.count) || 0);
      return Math.max(0, Math.min(n, this.emailOptions.max_per_run, this.emailAvailable()));
    },

    emailAvailable() {
      if (this.emailPreview && typeof this.emailPreview.available === 'number') return this.emailPreview.available;
      const c = this.emailOptions.counts;
      return this.emailForm.source === 'all' ? c.pending : ((c.pending_by_source || {})[this.emailForm.source] || 0);
    },

    emailEtaMinutes() {
      const avg = (Number(this.emailForm.delay_min) + Number(this.emailForm.delay_max)) / 2 || 30;
      return Math.max(1, Math.round((this.emailCount() * avg) / 60));
    },

    async refreshEmailPreview() {
      this.emailPreviewError = '';
      try {
        const res = await fetch('/api/email/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source: this.emailForm.source, niche: this.emailForm.niche, count: 3 })
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
        this.emailPreview = await res.json();
      } catch (e) {
        this.emailPreview = null;
        this.emailPreviewError = e.message || 'Не удалось собрать примеры писем';
      }
    },

    async toggleEmailTemplate() {
      const t = this.emailTemplate;
      t.open = !t.open;
      if (!t.open) return;
      try {
        const res = await fetch('/api/email/template');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        t.subjects = data.subjects.join('\n');
        t.body = data.body;
      } catch (e) {
        t.open = false;
        this.showToast('Ошибка', 'Не удалось загрузить текст письма', 'error');
      }
    },

    async saveEmailTemplate() {
      const t = this.emailTemplate;
      t.saving = true;
      try {
        const res = await fetch('/api/email/template', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ subjects: t.subjects.split('\n'), body: t.body })
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
        this.showToast('Текст сохранён', 'Новые письма пойдут с этим текстом', 'success');
        await this.refreshEmailPreview();
      } catch (e) {
        this.showToast('Не сохранено', e.message, 'error');
      } finally {
        t.saving = false;
      }
    },

    async startEmail() {
      const count = this.emailCount();
      if (!count) return;
      this.actionLoading = true;
      try {
        const res = await fetch('/api/email/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            count, source: this.emailForm.source, niche: this.emailForm.niche,
            delay_min: Number(this.emailForm.delay_min) || null,
            delay_max: Number(this.emailForm.delay_max) || null
          })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status === 'rejected') {
          this.showToast('Не запущено', data.detail || 'Рассылка уже идёт или параметры некорректны, подробности в журнале', 'warning');
          return;
        }
        this.showToast('Рассылка запущена', `Отправляю до ${count} писем`, 'info');
        await this.fetchStatus();
      } catch (e) {
        this.showToast('Ошибка запуска', e.message, 'error');
      } finally {
        this.actionLoading = false;
      }
    },

    async sendEmailTest() {
      const to = (this.emailForm.testTo || '').trim();
      if (!to) return;
      try { localStorage.setItem('lure_email_test_to', to); } catch (e) {}
      this.emailTesting = true;
      try {
        const res = await fetch('/api/email/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ to, source: this.emailForm.source, niche: this.emailForm.niche })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.success) {
          this.showToast('Тестовое письмо', `Отправлено на ${to}`, 'success');
        } else {
          this.showToast('Тест не прошёл', data.error || data.detail || 'Gmail отклонил письмо', 'error');
        }
      } catch (e) {
        this.showToast('Ошибка', e.message, 'error');
      } finally {
        this.emailTesting = false;
      }
    },

    // ---------- First-run setup wizard ----------
    async loadSetup(openIfIncomplete = false) {
      try {
        const res = await fetch('/api/setup/status');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        this.setup = await res.json();
        if (!this.setupOpen) {
          this.setupForm.sender = this.setup.email.sender;
          this.setupForm.sender_name = this.setup.email.sender_name;
          this.setupForm.wa_api_url = this.setup.whatsapp.api_url;
          this.setupForm.wa_profile_1 = this.setup.whatsapp.profile_1;
          this.setupForm.wa_profile_2 = this.setup.whatsapp.profile_2;
        }
        if (openIfIncomplete && !this.setup.completed) this.openSetup();
      } catch (e) {
        console.error(e);
      }
    },

    async openSetup(step = null) {
      await this.loadSetup();
      this.setupError = '';
      this.setupForm.app_password = '';
      this.setupStep = step !== null ? step : this.firstUnfinishedStep();
      this.setupOpen = true;
    },

    stepDone(id) {
      const s = this.setup;
      if (id === 'chrome') return s.chrome.ok;
      if (id === 'email') return s.email.ok;
      if (id === 'accounts') return s.logins.fb.ok && s.logins.ig.ok;
      return s.whatsapp.ok;
    },

    firstUnfinishedStep() {
      const i = this.setupSteps.findIndex(st => !this.stepDone(st.id));
      return i === -1 ? 0 : i;
    },

    setupMissing() {
      // WhatsApp is optional, so it never counts as missing
      return this.setupSteps.filter(st => st.id !== 'whatsapp' && !this.stepDone(st.id)).length;
    },

    async saveSetupEmail() {
      this.setupError = '';
      this.setupBusy = true;
      try {
        const res = await fetch('/api/setup/email', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sender: this.setupForm.sender,
            app_password: this.setupForm.app_password,
            sender_name: this.setupForm.sender_name
          })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
          this.setupError = data.error || data.detail || 'Не удалось сохранить почту. Попробуйте ещё раз.';
          return;
        }
        this.setupForm.app_password = '';
        this.showToast('Почта подключена', `Вход в ${this.setupForm.sender} проверен`, 'success');
        await this.loadSetup();
        this.setupStep = Math.min(this.setupStep + 1, this.setupSteps.length - 1);
      } catch (e) {
        this.setupError = `Нет связи с Lure: ${e.message}`;
      } finally {
        this.setupBusy = false;
      }
    },

    async confirmLogin(channel, confirmed) {
      try {
        await fetch('/api/setup/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ channel, confirmed })
        });
        await this.loadSetup();
      } catch (e) {
        this.showToast('Ошибка', e.message, 'error');
      }
    },

    async saveSetupWhatsApp() {
      this.setupError = '';
      this.setupBusy = true;
      try {
        const res = await fetch('/api/setup/whatsapp', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            api_url: this.setupForm.wa_api_url,
            profile_1: this.setupForm.wa_profile_1,
            profile_2: this.setupForm.wa_profile_2
          })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
          this.setupError = data.error || data.detail || 'Не удалось сохранить профили WhatsApp.';
          return;
        }
        this.showToast('WhatsApp', 'Профили сохранены', 'success');
        await this.loadSetup();
      } catch (e) {
        this.setupError = `Нет связи с Lure: ${e.message}`;
      } finally {
        this.setupBusy = false;
      }
    },

    async finishSetup() {
      try {
        await fetch('/api/setup/done', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ completed: true })
        });
        this.setup.completed = true;
        this.setupOpen = false;
        this.showToast('Настройка завершена', 'Можно запускать поиск и рассылку', 'success');
      } catch (e) {
        this.showToast('Ошибка', e.message, 'error');
      }
    },

    async stopAction(target = 'all') {
      try {
        await fetch('/api/action/stop', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target })
        });
        if (target === 'all' || target === 'meta_scrape') this.isScraping = false;
        if (target === 'all' || target === 'gmaps_scrape') this.isScrapingGmaps = false;
        this.playStopSound();
        this.showToast('Остановка', `Процесс ${target.toUpperCase()} экстренно остановлен`, 'warning');
        await this.fetchStatus();
        await this.fetchMetrics();
      } catch (e) {
        console.error(e);
      }
    },

    async openBrowser(channel, url = null) {
      try {
        const chMeta = this.getChannelMeta(channel);
        this.showToast('Открытие браузера...', `Запуск профиля для ${chMeta.name}`, 'info');
        const res = await fetch('/api/action/open_browser', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ channel, url })
        });
        const data = await res.json();
        if (data.success) {
          this.showToast('Браузер открыт', data.message || `Окно ${chMeta.name} успешно открыто`, 'success');
        } else {
          this.showToast('Внимание', data.error || 'Не удалось открыть браузер', 'warning');
        }
      } catch (e) {
        console.error(e);
        this.showToast('Ошибка сети', e.message, 'warning');
      }
    },

    openLeadBrowser(lead) {
      if (!lead) return;
      const platform = (lead.platform || '').toLowerCase();
      let ch = 'fb';
      if (platform.includes('ig') || platform.includes('insta')) ch = 'ig';
      else if (platform.includes('wa2') || platform.includes('2')) ch = 'wa2';
      else if (platform.includes('wa') || platform.includes('1')) ch = 'wa1';
      else if (platform.includes('fb')) ch = 'fb';

      const targetUrl = lead.profile_url || (lead.contact_info && lead.contact_info.startsWith('http') ? lead.contact_info : null);
      this.openBrowser(ch, targetUrl);
    },

    statusLabel(status) {
      const labels = {
        READY_TO_SEND: 'В очереди',
        STEP_1_SENT: 'Отправлено',
        ROUTED_WA: 'Ушёл в WhatsApp',
        EXISTING_CHAT: 'Уже есть переписка',
        FAILED_SEND: 'Ошибка отправки',
        SKIPPED_BY_USER: 'Пропущен вами',
        DISQUALIFIED_HAS_WEBSITE: 'Отсеян: есть сайт',
        DISQUALIFIED_LOW_AUDIENCE: 'Отсеян: мало подписчиков',
        DISQUALIFIED_NICHE_MISMATCH: 'Отсеян: не та ниша',
        DISQUALIFIED_NO_NICHE_SIGNAL: 'Отсеян: нет признаков ниши',
        DISQUALIFIED_ECOMMERCE_BLACKLIST: 'Отсеян: интернет-магазин',
        DISQUALIFIED_UNAVAILABLE: 'Отсеян: аккаунт недоступен',
      };
      return labels[status] || status;
    },

    statusClass(status) {
      if (status === 'READY_TO_SEND') return 'bg-[#E8F2FF] text-[#0066CC]';
      if (status === 'STEP_1_SENT') return 'bg-[#DCFCE7] text-[#15803D]';
      if (status === 'ROUTED_WA') return 'bg-[#E6F7EC] text-[#16A34A]';
      if (status === 'FAILED_SEND') return 'bg-[#FEE2E2] text-[#B91C1C]';
      if ((status || '').startsWith('DISQUALIFIED')) return 'bg-[#FFF4E0] text-[#B26A00]';
      return 'bg-[#F2F2F7] text-[#6E6E73]';
    },

    async makeMockup(lead) {
      if (!lead || !lead.id) return;
      this.mockupLeadId = `${lead.source}:${lead.id}`;
      try {
        const res = await fetch('/api/leads/mockup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source: lead.source, lead_id: lead.id })
        });
        const data = await res.json();
        if (data.success) {
          this.showToast('Макет готов', `Картинка для "${lead.business_name || lead.name}" на рабочем столе`, 'success');
        } else {
          this.showToast('Макет не создан', data.error || 'Ошибка генерации', 'warning');
        }
      } catch (e) {
        console.error(e);
        this.showToast('Ошибка сети', 'Не удалось связаться с сервером', 'warning');
      } finally {
        this.mockupLeadId = null;
      }
    },

    async resetLead(source, leadId) {
      try {
        const res = await fetch('/api/leads/reset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source, lead_id: leadId })
        });
        if (res.ok) {
          this.showToast('Сброс лида', `Лид #${leadId} возвращен в очередь`, 'info');
          await this.fetchLeads();
          await this.fetchMetrics();
        }
      } catch (e) {
        console.error(e);
      }
    },

    getChannel(ch) {
      return this.status.channels[ch] || { running: false, status_text: 'Готов к запуску', step: 'idle', current_lead: null, cooldown_remaining: 0 };
    },

    getChannelStatusClass(ch) {
      const c = this.getChannel(ch);
      if (c.step === 'cooldown' || c.cooldown_remaining > 0) return 'status-cooldown';
      if (c.running) return 'status-running';
      if (this.metrics[ch] && this.metrics[ch].sent_today >= this.metrics[ch].daily_limit) return 'status-limit';
      return 'status-ready';
    },

    getChannelStatusText(ch) {
      const c = this.getChannel(ch);
      if (c.step === 'cooldown' && c.cooldown_remaining > 0) {
        const m = Math.floor(c.cooldown_remaining / 60);
        const s = c.cooldown_remaining % 60;
        return `Защита: ${m}:${s < 10 ? '0' : ''}${s}`;
      }
      if (c.running) {
        return c.status_text || 'В процессе';
      }
      if (this.metrics[ch] && this.metrics[ch].sent_today >= this.metrics[ch].daily_limit) {
        return 'Лимит 24ч';
      }
      return 'Готов к запуску';
    },

    getScriptVision(ch) {
      const c = this.getChannel(ch);
      const text = (c.status_text || '').toLowerCase();

      // 1. Error state detection
      if (text.includes('ошибк') || text.includes('error') || text.includes('failed') || text.includes('краш')) {
        return {
          state: 'error',
          stepNum: 3,
          stepTitle: 'Диагностика сбоя шага',
          errorReason: c.status_text || 'Ошибка взаимодействия с элементом страницы',
          vision: 'Элемент диалога не ответил или сессия браузера требует подтверждения.',
          action: 'Лид помечен как "Пропущен". Скрипт автоматически продолжит со следующего контакта.'
        };
      }

      // 2. Cooldown safety state
      if (c.step === 'cooldown' || c.cooldown_remaining > 0) {
        const m = Math.floor(c.cooldown_remaining / 60);
        const s = c.cooldown_remaining % 60;
        const timeStr = `${m}:${s < 10 ? '0' : ''}${s}`;
        const prevName = c.current_lead ? c.current_lead.name : 'предыдущему контакту';
        const cdVision = 'Браузер выдерживает обязательную анти-спам паузу безопасности (≥5 мин).';
        return {
          state: 'cooldown',
          stepNum: 5,
          stepTitle: `Пауза безопасности (осталось ${timeStr})`,
          timeStr: timeStr,
          vision: cdVision,
          prevStep: `Сообщение для ${prevName} успешно доставлено`,
          nextStep: `Переход к следующему кандидату из очереди через ${timeStr}`
        };
      }

      // 3. Active Running state
      if (c.running) {
        if (c.step === 'scraping') {
          return {
            state: 'running',
            stepNum: 2,
            stepTitle: 'Поиск рекламодателей в Meta Ads Library',
            vision: 'Просмотр объявлений выбранных ниш и гео, отсев компаний с сайтом.',
            currentAction: c.status_text || 'Открытие карточек объявлений и проверка страниц рекламодателей...',
            nextStep: 'Сохранение квалифицированных лидов в leads.db'
          };
        }
        if (c.step === 'connecting') {
          return {
            state: 'running',
            stepNum: 1,
            stepTitle: 'Подключение к браузеру (CDP)',
            vision: 'Запуск Google Chrome / BitBrowser в изолированном скрытом процессе без окон CMD.',
            currentAction: 'Инициализация порта и проверка доступности сессии аккаунта...',
            nextStep: 'Открытие профиля целевого контакта и проверка доступности диалога'
          };
        }
        if (c.step === 'warmup') {
          return {
            state: 'running',
            stepNum: 2,
            stepTitle: 'Прогрев сессии и проверка профиля',
            vision: 'Имитация присутствия человека (dwell time 5-10с, скролл страницы, проверка кнопки сообщения).',
            currentAction: 'Осмотр шапки профиля и детекция истории предыдущей переписки...',
            nextStep: 'Активация поля ввода и генерация уникального Spintax-хука'
          };
        }
        if (c.step === 'typing') {
          return {
            state: 'running',
            stepNum: 3,
            stepTitle: 'Посимвольный ввод текста (Spintax)',
            vision: 'Эмуляция клавиатурного ввода с естественными микропаузами и редкими опечатками человека.',
            currentAction: c.current_hook ? `Печать вопроса: "${c.current_hook.slice(0, 50)}..."` : 'Посимвольный ввод сообщения в диалог...',
            nextStep: 'Финальная верификация текста и клик кнопки отправки'
          };
        }
        if (c.step === 'sent') {
          return {
            state: 'running',
            stepNum: 4,
            stepTitle: 'Отправка и проверка доставки',
            vision: 'Сообщение передано в сеть, проверка серверных галочек подтверждения доставки.',
            currentAction: 'Ожидание фиксации статуса доставлено...',
            nextStep: 'Запись статуса в базу и переход в паузу безопасности'
          };
        }
        return {
          state: 'running',
          stepNum: 2,
          stepTitle: c.status_text || 'Выполнение процесса',
          vision: c.status_text || 'Обработка страницы контакта...',
          currentAction: c.status_text || 'Скрипт выполняет действие...',
          nextStep: 'Подготовка следующего шага'
        };
      }

      // 4. Idle / Ready
      const readyCount = (this.metrics[ch] && this.metrics[ch].ready_leads) || 0;
      const defaultNext = readyCount > 0 ? `Взятие первого лида из очереди (всего готово: ${readyCount})` : 'Сбор новых лидов в базу через кнопку "Поиск Meta Ads"';
      return {
        state: 'idle',
        stepNum: 0,
        stepTitle: 'Система в режиме ожидания',
        vision: 'Браузер в спящем режиме. Консольные окна скрыты.',
        currentAction: 'Ожидание команды оператора.',
        nextStep: defaultNext
      };
    },

    get filteredLogs() {
      if (!this.logFilter || this.logFilter === 'ALL') {
        return this.logs;
      }
      const filter = (this.logFilter || '').toUpperCase();
      if (filter === 'WA') {
        return this.logs.filter(l => l.channel === 'WA' || l.channel === 'WA1' || l.channel === 'WA2');
      }
      if (filter === 'FB') {
        return this.logs.filter(l => l.channel === 'FB' || l.channel === 'META');
      }
      if (filter === 'IG') {
        return this.logs.filter(l => l.channel === 'IG' || l.channel === 'META');
      }
      return this.logs.filter(l => l.channel === filter);
    },

    getChannelLogs(ch) {
      const code = ch.toUpperCase();
      return this.logs.filter(l => l.channel === code || (code === 'META' && (l.channel === 'FB' || l.channel === 'IG'))).slice(-20);
    },

    getChannelMeta(ch) {
      if (ch === 'fb') {
        return { name: 'Facebook Messenger', sub: (this.metrics.fb && this.metrics.fb.target) || 'Meta Ads', limit: 10, color: '#0066CC', bg: '#E8F2FF', icon: 'globe' };
      }
      if (ch === 'ig') {
        return { name: 'Instagram Direct', sub: (this.metrics.ig && this.metrics.ig.target) || 'Meta Ads', limit: 10, color: '#E1306C', bg: '#FCE8EF', icon: 'camera' };
      }
      if (ch === 'wa1') {
        return { name: 'WhatsApp #1', sub: 'WhatsApp', limit: 6, color: '#16A34A', bg: '#E8F8EE', icon: 'message-circle' };
      }
      if (ch === 'wa2') {
        return { name: 'WhatsApp #2', sub: 'WhatsApp', limit: 6, color: '#0D9488', bg: '#E6F7F5', icon: 'message-square' };
      }
      return { name: ch, sub: '', limit: 10, color: '#0066CC', bg: '#E8F2FF', icon: 'activity' };
    },

    getRingOffset(percentage) {
      const circ = 175.92;
      const pct = Math.min(100, Math.max(0, Number(percentage) || 0));
      return circ - (circ * pct / 100);
    }
  }));
}

document.addEventListener('alpine:init', registerHubApp);
if (window.Alpine) {
  registerHubApp();
}
