/* ===================================================================
   股票助手 · 应用主逻辑
   - 全局状态 / 数据加载 (fetch + fallback)
   - 路由与导航 (hash)
   - 日期选择器 (归档日历)
   - Markdown 渲染器
   - 7 个页面渲染
   A股约定: 红涨绿跌 (RED=#ef4444 涨, GREEN=#22c55e 跌)
   =================================================================== */
(function () {
  'use strict';

  /* ===================== API 配置 ===================== */
  // v4: 优先从 API 服务获取数据（本地开发或 VPS），回退到静态 JSON（GitHub Pages）
  var API_BASE = '';  // 空字符串=同源, 或填 'http://localhost:8765'
  var API_AVAILABLE = false;  // 运行时检测
  var AUTO_REFRESH_INTERVAL = 60000;  // 自动刷新间隔（ms），盘中每分钟
  var autoRefreshTimer = null;

  /* ===================== 全局状态 ===================== */
  var appState = {
    currentDate: null,   // null = 最新
    currentType: null,   // null = 最新
    latestData: null,
    manifest: null,
    historyData: { goldStocks: null, heatTracking: null },
    activeArchive: null, // 当前加载的归档数据
    hiddenSectors: [],
    calCursor: null      // 日历页游标 {year, month}
  };

  /* ===================== 报告类型配置 ===================== */
  var REPORT_TYPES = [
    { key: 'morning', label: '晨报' },
    { key: 'noon', label: '午报' },
    { key: 'evening', label: '晚报' },
    { key: 'saturday', label: '周六复盘' },
    { key: 'sunday', label: '周日展望' }
  ];

  /* ===================== 工具函数 ===================== */
  function fetchJSON(path) {
    return fetch(path + '?t=' + Date.now()).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  // v4: 从 API 服务获取数据
  function fetchAPI(endpoint) {
    var url = API_BASE + endpoint;
    return fetch(url + (url.indexOf('?') > -1 ? '&' : '?') + 't=' + Date.now())
      .then(function (r) {
        if (!r.ok) throw new Error('API HTTP ' + r.status);
        return r.json();
      });
  }

  // v4: 检测 API 是否可用
  function detectAPI() {
    return fetch(API_BASE + '/api/health?t=' + Date.now(), { timeout: 3000 })
      .then(function (r) {
        if (r.ok) {
          API_AVAILABLE = true;
          console.log('[App] API 服务可用，启用实时数据模式');
        }
        return r.ok;
      })
      .catch(function () {
        API_AVAILABLE = false;
        console.log('[App] API 服务不可用，使用静态 JSON 模式');
        return false;
      });
  }

  // v4: 自动刷新看板数据（仅在 API 可用且市场交易时间内）
  function startAutoRefresh() {
    if (autoRefreshTimer) clearInterval(autoRefreshTimer);
    autoRefreshTimer = setInterval(function () {
      var now = new Date();
      var mins = now.getHours() * 60 + now.getMinutes();
      var day = now.getDay();
      var isWeekday = day >= 1 && day <= 5;
      var inSession = isWeekday && ((mins >= 570 && mins <= 690) || (mins >= 780 && mins <= 900));
      if (API_AVAILABLE && inSession) {
        console.log('[App] 盘中自动刷新...');
        fetchAPI('/api/dashboard?period=morning').then(function (data) {
          if (data && data.indices) {
            appState.latestData = transformAPIData(data);
            renderDashboard(appState.latestData);
            updateLastUpdate();
            updateMarketStatus();
          }
        }).catch(function (e) {
          console.warn('[App] 自动刷新失败', e);
        });
      }
      updateMarketStatus();
    }, AUTO_REFRESH_INTERVAL);
  }

  // v4: 将 API 返回的数据转换为前端兼容格式
  function transformAPIData(apiData) {
    if (!apiData) return null;
    var indices = (apiData.indices || []).map(function (idx) {
      return {
        name: idx.name,
        value: String(Math.round(idx.close) || '--'),
        change: (idx.pct_chg > 0 ? '+' : '') + Number(idx.pct_chg || 0).toFixed(2) + '%'
      };
    });
    var northVal = apiData.north_money && apiData.north_money.north_money;
    var northFlow = northVal != null ?
      (northVal >= 0 ? '净流入' : '净流出') + Math.abs(northVal).toFixed(2) + '亿' : '';
    return {
      date: apiData.date,
      type: apiData.period || 'morning',
      title: '多维市场研报（' + (apiData.period === 'morning' ? '晨报' : apiData.period === 'noon' ? '午报' : '晚报') + '）',
      score: 0,
      market: {
        indices: indices,
        limit_up: apiData.stats ? apiData.stats.limit_up_count : 0,
        limit_down: 0,
        volume: '',
        north_flow: northFlow
      },
      _apiData: apiData  // 保留原始 API 数据供 v4 页面使用
    };
  }
  function safe(v, d) { return (v === undefined || v === null) ? (d === undefined ? '--' : d) : v; }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function num(n, d) {
    if (n === null || n === undefined || n === '' || isNaN(Number(n))) return '--';
    return Number(n).toFixed(d == null ? 2 : d);
  }
  // 涨跌着色: 正/涨=红(up), 负/跌=绿(down)
  function changeClass(change) {
    if (change === null || change === undefined || change === '') return '';
    var s = String(change).trim();
    var n = parseFloat(s);
    if (isNaN(n)) {
      if (s.indexOf('+') === 0 || s.indexOf('涨') !== -1 || s.indexOf('红') !== -1) return 'up';
      if (s.indexOf('-') === 0 || s.indexOf('跌') !== -1 || s.indexOf('绿') !== -1) return 'down';
      return '';
    }
    return n > 0 ? 'up' : (n < 0 ? 'down' : '');
  }
  function changeStr(c) {
    if (c === null || c === undefined || c === '') return '--';
    var s = String(c);
    var n = parseFloat(s);
    if (!isNaN(n) && n > 0 && s.charAt(0) !== '+' && s.charAt(0) !== '-') return '+' + s;
    return s;
  }
  function fmtDate(d) {
    // d: "2026-06-25" 或 "20260625"
    if (!d) return '--';
    var s = String(d);
    if (/^\d{8}$/.test(s)) return s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8);
    return s;
  }
  function heatClass(h) { return (h != null && h >= 0) ? 'pos' : 'neg'; }
  function badgeClass(state) {
    if (!state) return 'badge-cooling';
    if (state.indexOf('高潮') !== -1) return 'badge-climax';
    if (state.indexOf('崛起') !== -1) return 'badge-rising';
    return 'badge-cooling';
  }

  // v5: 今日日期字符串 (YYYY-MM-DD)
  function todayStr() {
    var d = new Date();
    var m = '' + (d.getMonth() + 1);
    var day = '' + d.getDate();
    return d.getFullYear() + '-' + (m.length < 2 ? '0' + m : m) + '-' + (day.length < 2 ? '0' + day : day);
  }
  // v5: 看板当前使用的日期 (优先 currentDate，其次 latestData.date，最后今天)
  function dashboardDate() {
    return appState.currentDate || (appState.latestData && appState.latestData.date) || todayStr();
  }
  // v5: 兼容提取列表 (API 返回 {data:[...]} 或裸数组)
  function extractList(resp) {
    if (Array.isArray(resp)) return resp;
    if (resp && Array.isArray(resp.data)) return resp.data;
    return [];
  }

  /* ===================================================================
     内置回退数据 (data/ 目录无文件时使用, 保证站点开箱即用)
     结构与 data/latest.json / data/history/*.json 一致
     =================================================================== */
  var FALLBACK_MANIFEST = {
    latest_date: '2026-06-25',
    latest_type: 'evening',
    updated_at: '2026-06-25 20:00:00',
    archives: [
      { date: '2026-06-25', type: 'morning', title: '多维市场研报（晨报）' },
      { date: '2026-06-25', type: 'noon', title: '多维市场研报（午报）' },
      { date: '2026-06-25', type: 'evening', title: '多维市场研报（晚报）' },
      { date: '2026-06-24', type: 'evening', title: '多维市场研报（晚报）' },
      { date: '2026-06-23', type: 'evening', title: '多维市场研报（晚报）' }
    ]
  };

  function buildFallbackHeat() {
    // 与 data/history/heat_tracking.json 同构 (20交易日 10板块)
    var labels = ['05-28','05-29','06-01','06-02','06-03','06-04','06-05','06-08','06-09','06-10','06-11','06-12','06-15','06-16','06-17','06-18','06-22','06-23','06-24','06-25'];
    var dates = labels.map(function(_, i) { return '2026' + _replace(labels[i]); });
    // 各板块热度序列 (节选自真实 heat_data)
    var defs = [
      { name: 'AI算力', heat: [85.0,-55.6,-46.7,83.9,76.1,83.3,-51.7,-54.4,95.6,-49.4,-43.9,-52.2,100.0,-19.5,91.7,75.6,-39.4,-51.7,87.8,82.8], cur: 82.8, st: '高潮', tr: '↑↑', desc: '热度83处于高位，资金持续流入' },
      { name: '半导体芯片', heat: [90.3,-56.0,-44.9,85.5,-37.9,89.5,-51.2,-54.4,100.0,-46.5,-25.7,-50.4,100.0,-31.3,98.2,77.5,-39.3,-51.2,95.9,89.5], cur: 89.5, st: '高潮', tr: '↑↑', desc: '热度90处于高位，资金持续流入' },
      { name: '消费电子', heat: [-11.0,-54.5,-45.7,83.0,-41.8,84.6,-45.0,-54.5,83.8,-56.0,-42.6,-49.7,100.0,-29.9,-12.6,-30.8,-42.6,-56.0,79.0,79.0], cur: 79.0, st: '高潮', tr: '↑↑', desc: '热度79处于高位，资金持续流入' },
      { name: '新能源', heat: [38.8,-55.6,27.6,-54.3,-50.6,-52.5,-51.2,-56.9,56.7,-47.4,-49.9,7.1,-1.9,-1.0,-49.9,-51.2,85.8,-49.9,-44.9,-49.3], cur: -49.3, st: '退烧', tr: '↓', desc: '热度-49有所回落' },
      { name: '机器人', heat: [74.8,-54.6,-32.4,-49.7,-53.6,-50.6,-44.7,-50.1,75.3,-54.6,-56.1,-53.1,88.6,53.1,-51.6,-50.6,8.2,-51.1,-48.7,-50.1], cur: -50.1, st: '退烧', tr: '↓', desc: '热度-50有所回落' },
      { name: '低空经济', heat: [-6.1,-60.0,-10.1,-21.8,-16.2,-45.9,25.9,-52.9,-14.6,-31.5,-4.2,34.4,-9.3,-46.0,16.6,-27.5,-36.2,-56.5,4.4,-43.8], cur: -43.8, st: '退烧', tr: '↓', desc: '热度-44有所回落' },
      { name: '医药生物', heat: [-59.2,58.5,-32.2,-60.0,-57.5,-60.0,-54.0,-57.5,-7.4,-13.2,-42.9,64.2,-57.5,-58.3,-54.1,56.8,1.3,5.8,-55.1,-34.6], cur: -34.6, st: '崛起', tr: '↑', desc: '热度-35上升中' },
      { name: '军工航天', heat: [46.4,-54.2,-9.6,-54.2,-55.1,-54.2,-52.2,-56.1,57.5,-57.1,-58.1,43.4,-34.5,-5.6,-52.2,-52.2,73.6,-56.1,-48.3,-53.2], cur: -53.2, st: '退烧', tr: '↓', desc: '热度-53有所回落' },
      { name: '汽车智驾', heat: [-49.5,-58.6,78.3,-52.2,-53.7,-54.4,-52.2,-51.5,-30.0,-52.9,-57.2,-12.9,74.7,-50.1,-54.4,-6.2,-12.1,-52.2,-52.9,-57.2], cur: -57.2, st: '退烧', tr: '↓', desc: '热度-57连续下降，资金净流出' },
      { name: '金融科技', heat: [-55.9,-59.0,82.3,-53.9,-58.0,-57.0,-53.4,-30.8,67.1,67.1,-58.0,68.1,-13.4,-51.9,-58.0,-54.9,81.3,-52.9,-53.9,-57.0], cur: -57.0, st: '退烧', tr: '↓', desc: '热度-57连续下降，资金净流出' }
    ];
    function _replace(lbl) { return lbl.replace('-', ''); }
    // 生成资本与涨停序列 (基于热度反向生成可读数据)
    defs.forEach(function (d) {
      d.heat_series = d.heat;
      d.capital_series = d.heat.map(function (h) { return Math.round(h * 42000 + (Math.random() - 0.5) * 2000000); });
      d.limit_series = d.heat.map(function (h) { return Math.max(0, Math.round(h / 4 + 25 + (Math.random() - 0.5) * 10)); });
      d.current_heat = d.cur;
      d.lifecycle = { state: d.st, trend: d.tr, description: d.desc };
    });
    return { trade_dates: dates, date_labels: labels, sectors: defs, generated_at: '2026-06-25 18:24' };
  }

  var FALLBACK_GOLD = {
    summary: { total: 12, win_rate: 0.583, avg_return: 4.32, avg_max_draw: -3.86 },
    stocks: [
      { name: '万丰奥威', code: '002085', first_date: '2026-05-21', count: 4, ret_1d: 3.21, ret_3d: 7.85, ret_5d: 12.40, max_gain: 18.62, max_draw: -2.31, reason: '低空经济+业绩+资金共振' },
      { name: '兆易创新', code: '603986', first_date: '2026-06-12', count: 2, ret_1d: 9.98, ret_3d: 15.23, ret_5d: 21.07, max_gain: 24.50, max_draw: -1.05, reason: '存储芯片龙头+美光催化+机构净买' },
      { name: '长电科技', code: '600584', first_date: '2026-06-18', count: 1, ret_1d: 10.02, ret_3d: 8.74, ret_5d: 6.31, max_gain: 12.88, max_draw: -3.42, reason: '先进封装+AI算力链+龙虎榜机构+41亿' },
      { name: '聚辰股份', code: '688123', first_date: '2026-06-18', count: 1, ret_1d: 20.00, ret_3d: 26.51, ret_5d: 19.74, max_gain: 31.20, max_draw: -5.18, reason: '存储芯片+科创板20cm+机构净买' },
      { name: '德明利', code: '001269', first_date: '2026-06-18', count: 1, ret_1d: 10.01, ret_3d: 22.36, ret_5d: 28.90, max_gain: 33.45, max_draw: -4.20, reason: '存储模组+机构净买+24亿' },
      { name: '烽火通信', code: '600498', first_date: '2026-06-10', count: 2, ret_1d: 1.85, ret_3d: -2.10, ret_5d: 3.42, max_gain: 6.78, max_draw: -7.32, reason: '光通信+光纤+芯片多维催化' },
      { name: '三环集团', code: '300408', first_date: '2026-06-05', count: 1, ret_1d: 4.32, ret_3d: 9.18, ret_5d: 5.61, max_gain: 13.25, max_draw: -3.88, reason: '电子陶瓷+AI半导体设备扩散' },
      { name: '苏州固锝', code: '002079', first_date: '2026-06-12', count: 1, ret_1d: 3.52, ret_3d: 6.41, ret_5d: 4.18, max_gain: 9.87, max_draw: -2.95, reason: '半导体二极管+龙虎榜+氦气逻辑' },
      { name: '新易盛', code: '300502', first_date: '2026-05-28', count: 3, ret_1d: -1.20, ret_3d: 2.15, ret_5d: -3.40, max_gain: 5.62, max_draw: -8.74, reason: '光模块+业绩见顶分歧' },
      { name: '中际旭创', code: '300308', first_date: '2026-06-05', count: 1, ret_1d: 2.18, ret_3d: 4.92, ret_5d: 1.30, max_gain: 7.45, max_draw: -4.12, reason: '光模块龙头+存储+MLCC催化' },
      { name: '江海股份', code: '002484', first_date: '2026-06-12', count: 1, ret_1d: 10.00, ret_3d: 7.85, ret_5d: 11.23, max_gain: 15.67, max_draw: -2.88, reason: '铝电解电容涨价+服务器电源订单' },
      { name: '拓荆科技', code: '688072', first_date: '2026-06-10', count: 1, ret_1d: 5.62, ret_3d: 8.91, ret_5d: 12.45, max_gain: 16.78, max_draw: -3.55, reason: '半导体设备+晶圆厂扩产+机构调研' }
    ]
  };

  function buildFallbackLatest() {
    var heat = buildFallbackHeat();
    var top = heat.sectors.slice().sort(function (a, b) { return b.current_heat - a.current_heat; }).slice(0, 5);
    return {
      date: '2026-06-25',
      type: 'evening',
      title: '多维市场研报（晚报）',
      summary: 'AI算力退潮，资金转向军工航天；半导体/存储芯片高潮延续，科创50领涨全场；电子板块成交额首破1.2万亿创历史新高。',
      market: {
        indices: [
          { name: '上证指数', value: 4120.28, change: '+0.23%' },
          { name: '深证成指', value: 16344.08, change: '+1.82%' },
          { name: '创业板指', value: 4371.99, change: '+2.84%' },
          { name: '科创50', value: 2066.33, change: '+3.87%' },
          { name: '沪深300', value: 5020.10, change: '+1.56%' },
          { name: '中证500', value: 8938.01, change: '+1.08%' }
        ],
        limit_up: 131,
        limit_down: 8,
        volume: '35900亿',
        up_count: 3812,
        down_count: 1245
      },
      heat: { trade_dates: heat.trade_dates, date_labels: heat.date_labels, sectors: heat.sectors },
      gold_stocks: [
        { name: '兆易创新', code: '603986', reason: '存储芯片龙头+美光催化+机构净买28亿', score: 92 },
        { name: '长电科技', code: '600584', reason: '先进封装+AI算力链+龙虎榜机构+41亿', score: 88 },
        { name: '聚辰股份', code: '688123', reason: '存储芯片+科创板20cm+机构净买17亿', score: 86 },
        { name: '德明利', code: '001269', reason: '存储模组+机构净买24亿+换手暴增', score: 84 },
        { name: '江海股份', code: '002484', reason: '铝电解电容涨价+服务器电源订单+日本大厂催化', score: 82 }
      ],
      report: { chapters: [], full_md: FALLBACK_REPORT_MD },
      score: 90,
      cls: FALLBACK_CLS,
      qsq: FALLBACK_QSQ,
      calendar: FALLBACK_CALENDAR
    };
  }

  /* ===================== 回退: 报告 Markdown ===================== */
  var FALLBACK_REPORT_MD =
'# 多维市场研报（晚报）\n\n' +
'> 交易日：2026-06-25（周四） | 数据截至：2026-06-25 15:58 | 信号来源：财联社API+Tushare+新浪HTTP\n\n' +
'---\n\n' +
'## 第零章：财联社信源扫描与信号提取\n\n' +
'### 一、电报信号扫描\n\n' +
'今日财联社电报覆盖时段为11:20至15:53，新增20条（当日累计40条）。本日电报无红色重要信号，但晚间时段释放多条值得关注的宏观信号：\n\n' +
'- **中东局势持续升温**：以色列总理称掌控黎巴嫩南部安全区不撤军，霍尔木兹海峡通航量恢复至战事前57%。\n' +
'- **全球通胀与货币政策**：美国5月PCE整体同比升至4.1%、核心PCE升至3.4%创近三年新高；黄金期货突破4050美元/盎司。\n' +
'- **科技巨头动态**：IBM开创"亚1纳米"芯片时代；高通发布HBC架构（带宽较HBM提升6倍），微软Azure确认部署。\n\n' +
'### 二、投资日历事件\n\n' +
'| 日期 | 事件 | 潜在影响 |\n' +
'|------|------|---------|\n' +
'| 6月25日 | 鸿蒙智行尊界品牌盛典 | 智能汽车/AI终端 |\n' +
'| 6月26日 | 集成电路峰会（深圳） | 半导体 |\n' +
'| 6月27日 | 亚洲机器人大会暨展览会 | 机器人 |\n\n' +
'### 热点生命周期判断\n\n' +
'| 热点 | 生命周期 | 判断依据 |\n' +
'|------|---------|---------|\n' +
'| 半导体/存储芯片 | **高潮** | 涨停板集中，机构龙虎榜超126亿净买入 |\n' +
'| AI算力/PCB/光通信 | **高潮** | 电子板块成交额1.2万亿创历史新高 |\n' +
'| 元器件/MLCC/电容 | **崛起** | 日本大厂涨价催化刚发酵，资金开始流入 |\n' +
'| 黄金 | **退烧** | 金价从高点回撤近30%，ETF大幅下挫 |\n\n' +
'---\n\n' +
'## 第一章：大盘概览与美股映射\n\n' +
'### 一、A股核心指数\n\n' +
'| 指数 | 收盘 | 涨跌幅 | 成交额(亿) |\n' +
'|------|------|--------|-----------|\n' +
'| 上证指数 | 4120.28 | +0.23% | 16190 |\n' +
'| 深证成指 | 16344.08 | +1.82% | 19753 |\n' +
'| 创业板指 | 4371.99 | +2.84% | 9765 |\n' +
'| 科创50 | 2066.33 | +3.87% | 2205 |\n\n' +
'**盘面解读**：今日A股呈现"深强沪弱"格局，科创50以+3.87%领涨全场，创业板指+2.84%紧随其后。科技成长与价值蓝筹分化加剧，两市合计成交约3.59万亿，连续维持高位量能。\n\n' +
'### 二、美股盘前动态\n\n' +
'隔夜美股收盘：道琼斯+0.65%，纳斯达克+0.44%，标普500+0.59%。盘前纳指期货小幅转跌（-0.12%）。美光科技财报炸裂、多家投行上调目标价至2000美元，直接催化今日A股存储芯片板块强势。\n\n' +
'---\n\n' +
'## 第二章：板块热度与资金流向\n\n' +
'### 一、板块生命周期总览\n\n' +
'当前市场核心热点集中在半导体/AI算力主线，存储芯片、先进封装、PCB、光通信构成机构资金核心配置方向。新能源、机器人、低空经济板块退烧，资金净流出明显。\n\n' +
'### 二、资金流向TOP5\n\n' +
'| 板块 | 主力净流入(亿) | 生命周期 |\n' +
'|------|---------------|---------|\n' +
'| AI算力 | +231.5 | 高潮 ↑↑ |\n' +
'| 半导体芯片 | +125.4 | 高潮 ↑↑ |\n' +
'| 消费电子 | +42.5 | 高潮 ↑↑ |\n' +
'| 医药生物 | -19.4 | 崛起 ↑ |\n' +
'| 新能源 | -175.0 | 退烧 ↓ |\n\n' +
'> 机构共识：AI投资热潮向上游扩散是当前最强产业叙事，半导体+电子材料+光通信+PCB构成机构资金的核心配置方向。\n\n' +
'---\n\n' +
'## 第三章：金股推荐与多维验证\n\n' +
'### 金股清单\n\n' +
'| 股票 | 代码 | 推荐理由 | 综合评分 |\n' +
'|------|------|---------|---------|\n' +
'| 兆易创新 | 603986 | 存储芯片龙头+美光催化+机构净买28亿 | 92 |\n' +
'| 长电科技 | 600584 | 先进封装+AI算力链+龙虎榜机构+41亿 | 88 |\n' +
'| 聚辰股份 | 688123 | 存储芯片+科创板20cm+机构净买17亿 | 86 |\n\n' +
'### 多维共振分析\n\n' +
'1. **业绩维度**：兆易创新Q1利润超预期，存储景气周期确认\n' +
'2. **资金维度**：龙虎榜机构净买入TOP5全部为科技股\n' +
'3. **催化维度**：美光炸裂财报+6/26集成电路峰会事件驱动\n' +
'4. **热度维度**：半导体板块热度90处于高潮，趋势↑↑\n\n' +
'> 交叉验证：龙虎榜机构净买入TOP5（长电+41亿、兆易+28亿、德明利+24亿、聚辰+17亿、TCL+15.75亿）与金股推荐高度重合，资金与逻辑共振确认。\n\n' +
'---\n\n' +
'## 第四章：操作建议与风险提示\n\n' +
'### 操作建议\n\n' +
'- **核心仓位**：存储芯片主线（兆易、德明利、聚辰）持有为主，回调不破5日线不减仓\n' +
'- **机动仓位**：先进封装/PCB（长电、广合科技）逢低介入\n' +
'- **观察仓位**：元器件涨价链（江海、艾华）等待二次确认\n\n' +
'### 风险提示\n\n' +
'1. 电子板块1.2万亿成交额创历史新高，需警惕短期情绪过热的回吐风险\n' +
'2. 美光财报利好已充分反映，谨防"利好出尽"回调\n' +
'3. 黄金大幅回撤可能拖累避险情绪，关注外围地缘变化\n';

  /* ===================== 回退: 财联社信源 ===================== */
  var FALLBACK_CLS = {
    vip_articles: [
      { type: '电报解读', read: 900739, title: 'AI算力需求传导，"纤维之王"对位芳纶步入高景气周期', summary: 'AI算力需求传导，对位芳纶有望深度受益AIDC光纤渗透率提升，分析师强Call。', stocks: ['特发信息', '法尔胜', '汉缆股份'] },
      { type: '九点特供', read: 1000643, title: '"五眼联盟"警告网络威胁将因AI增长，看好网络安全', summary: '算力、AI、液冷、服务器主线，网络安全行业有望迎来新周期。', stocks: ['广合科技', '美格智能', '寒武纪'] },
      { type: '公告全知道', read: 929316, title: '存储芯片+MLCC+超级电容+光模块+机器人+液冷', summary: '公司代理销售三星MLCC产品，多赛道叠加催化。', stocks: ['烽火通信', '中际旭创', '新易盛'] },
      { type: '风口研报', read: 903165, title: '日本大厂全线调涨铝电解电容价格', summary: '公司相关产品已在服务器电源领域获得批量订单，叠加铝电解电容涨价潮。', stocks: ['江海股份', '艾华集团', '烽火通信'] },
      { type: '数据研选', read: 903060, title: '全球算力服务继续通胀，价格端持续验证算力紧俏', summary: '算力租赁商业逻辑更加清晰，国内少数标的深度受益。', stocks: ['芯联集成', '台基股份', '捷捷微电'] },
      { type: '风口研报', read: 947807, title: '高端电子制造+存储半导体+计量智能终端', summary: '存储芯片封装测试等业务有望逐步发力，AI Agent催化。', stocks: ['东芯股份', '恒烁股份', '苏州固锝'] }
    ],
    telegraph: [
      { time: '15:53', text: '商务部：将出台新一轮稳外贸政策，支持跨境电商发展', important: false },
      { time: '14:30', text: '兆易创新涨停，机构龙虎榜净买入28.33亿元，存储芯片行情爆发', important: true },
      { time: '13:45', text: 'IBM开创"亚1纳米"芯片时代，主页热文阅读约40.9万次', important: false },
      { time: '11:20', text: '美光科技财报炸裂，多家投行上调目标价至2000美元', important: true },
      { time: '10:32', text: '电子板块成交额首次突破1.2万亿，创历史新高', important: true },
      { time: '09:55', text: '高通发布HBC架构，带宽较HBM提升6倍，微软Azure确认部署', important: false },
      { time: '09:30', text: 'A股开盘，科创50高开1.2%，存储芯片板块集体走强', important: false }
    ],
    discovery: [
      { name: '烽火通信', code: '600498.SH', board: '沪市主板', industry: '通信设备', match: 20, article: '光纤+芯片+光通信+磷化工+电子特气+先进封装' },
      { name: '寒武纪', code: '688256.SH', board: '科创板', industry: '半导体', match: 10, article: '人工智能+服务器算力主线' },
      { name: '广合科技', code: '001389.SZ', board: '深市主板', industry: '元器件', match: 10, article: '算力服务器关键部件PCB' },
      { name: '美格智能', code: '002881.SZ', board: '深市主板', industry: '元器件', match: 10, article: '高算力智能模组+AI算力' },
      { name: '芯联集成', code: '688469.SH', board: '科创板', industry: '半导体', match: 15, article: '功率半导体+模拟芯片晶圆代工' },
      { name: '中际旭创', code: '300308.SZ', board: '创业板', industry: '通信设备', match: 11, article: '光模块+光通信+芯片' },
      { name: '新易盛', code: '300502.SZ', board: '创业板', industry: '通信设备', match: 10, article: '光模块+光通信' },
      { name: '苏州固锝', code: '002079.SZ', board: '深市主板', industry: '电气设备', match: 10, article: '半导体+芯片二极管' }
    ]
  };

  /* ===================== 回退: 钱三强选股 ===================== */
  var FALLBACK_QSQ = {
    summary: { total: 5224, pass1: 170, pass2: 508, pass3: 1161, pass_all: 37 },
    selected_stocks: [
      { ts_code: '002975.SZ', name: '博杰股份', industry: '专用机械', close: 156.18, pct_chg: 3.21, turnover_rate: 14.18, jigou: 18908.48, youzi: 10039.57, ema55_angle: 48.64 },
      { ts_code: '000062.SZ', name: '深圳华强', industry: '元器件', close: 34.14, pct_chg: 3.83, turnover_rate: 8.54, jigou: 19033.01, youzi: 4382.50, ema55_angle: 15.77 },
      { ts_code: '002522.SZ', name: '浙江众成', industry: '塑料', close: 8.01, pct_chg: 6.52, turnover_rate: 15.50, jigou: 2433.90, youzi: 4158.09, ema55_angle: 34.95 },
      { ts_code: '002079.SZ', name: '苏州固锝', industry: '电气设备', close: 14.69, pct_chg: 3.52, turnover_rate: 10.56, jigou: 6119.37, youzi: 8552.12, ema55_angle: 39.17 },
      { ts_code: '002925.SZ', name: '盈趣科技', industry: '元器件', close: 24.60, pct_chg: 4.02, turnover_rate: 4.63, jigou: 3880.66, youzi: 2309.03, ema55_angle: 32.27 },
      { ts_code: '002559.SZ', name: '亚威股份', industry: '机床制造', close: 11.19, pct_chg: 2.85, turnover_rate: 15.17, jigou: 5237.84, youzi: 3356.74, ema55_angle: 17.54 },
      { ts_code: '000620.SZ', name: '盈新发展', industry: '全国地产', close: 3.11, pct_chg: 4.36, turnover_rate: 12.46, jigou: 2949.26, youzi: 36.40, ema55_angle: 9.76 },
      { ts_code: '002145.SZ', name: '钛能化学', industry: '化工原料', close: 5.02, pct_chg: 2.45, turnover_rate: 3.33, jigou: 4320.36, youzi: 820.93, ema55_angle: 19.32 }
    ],
    history: [
      { date: '2026-06-25', stocks: 37, win: 22, avg_ret: 3.85 },
      { date: '2026-06-24', stocks: 31, win: 18, avg_ret: 2.92 },
      { date: '2026-06-23', stocks: 28, win: 15, avg_ret: 1.78 },
      { date: '2026-06-22', stocks: 25, win: 14, avg_ret: 4.12 },
      { date: '2026-06-18', stocks: 41, win: 26, avg_ret: 5.34 }
    ]
  };

  /* ===================== 回退: 投资日历 ===================== */
  var FALLBACK_CALENDAR = {
    events: [
      { date: '2026-06-26', weekday: '周五', title: '集成电路峰会（深圳）', sector: '半导体', desc: '国产半导体全产业链大会，设备/材料/制造/设计', stocks: ['中芯国际', '北方华创', '中微公司'], hot: true },
      { date: '2026-06-27', weekday: '周六', title: '亚洲机器人大会暨展览会', sector: '机器人', desc: '人形机器人供应链集中展示，产能爬坡与场景验证', stocks: ['拓普集团', '三花智控', '绿的谐波'], hot: true },
      { date: '2026-06-29', weekday: '周一', title: '太空算力大会', sector: '算力基础设施', desc: '太空算力基础设施与卫星互联网发展', stocks: ['中国卫星', '航天电子'], hot: false },
      { date: '2026-06-30', weekday: '周二', title: 'MLF到期+逆回购', sector: '宏观流动性', desc: 'MLF到期3000亿，关注流动性投放情况', stocks: [], hot: false },
      { date: '2026-07-01', weekday: '周三', title: '新型能源体系十五五规划发布会', sector: '新能源', desc: '发改委/能源局印发十五五规划新闻发布会', stocks: ['隆基绿能', '宁德时代'], hot: false },
      { date: '2026-07-03', weekday: '周五', title: '美国6月非农就业数据', sector: '宏观/外围', desc: '美联储政策预期关键数据，影响外围情绪', stocks: [], hot: false }
    ]
  };

  /* ===================================================================
     数据加载
     =================================================================== */
  function loadManifest() {
    return fetchJSON('data/manifest.json').catch(function () {
      console.warn('[Data] manifest.json 加载失败, 使用回退数据');
      return FALLBACK_MANIFEST;
    });
  }
  function loadLatest() {
    return fetchJSON('data/latest.json').catch(function () {
      console.warn('[Data] latest.json 加载失败, 使用回退数据');
      return buildFallbackLatest();
    });
  }
  function loadArchive(date, type) {
    return fetchJSON('data/archive/' + date + '_' + type + '.json').catch(function (e) {
      console.warn('[Data] 归档加载失败: ' + date + '_' + type, e.message);
      return null;
    });
  }
  function loadHistory() {
    return Promise.all([
      fetchJSON('data/history/gold_stocks.json').catch(function () { return FALLBACK_GOLD; }),
      fetchJSON('data/history/heat_tracking.json').catch(function () { return buildFallbackHeat(); })
    ]).then(function (r) { return { goldStocks: r[0], heatTracking: r[1] }; });
  }

  /* ===================================================================
     Markdown 渲染器 (轻量 MD -> HTML)
     支持: # ## ### #### / **bold** / 表格 / 列表 / ```代码块 / --- / >
     =================================================================== */
  function renderMarkdown(md) {
    if (!md) return '<p class="empty-box">暂无报告内容</p>';
    var lines = String(md).replace(/\r\n/g, '\n').split('\n');
    var html = [];
    var i = 0;
    var inCode = false;
    var codeBuf = [];
    var listType = null; // 'ul' | 'ol'

    function flushList() {
      if (listType) { html.push('</' + listType + '>'); listType = null; }
    }
    function inline(s) {
      // 转义后处理内联
      s = esc(s);
      // bold
      s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      // 行内代码
      s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
      return s;
    }

    while (i < lines.length) {
      var line = lines[i];

      // 代码块
      if (/^```/.test(line)) {
        if (inCode) {
          html.push('<pre><code>' + esc(codeBuf.join('\n')) + '</code></pre>');
          codeBuf = []; inCode = false;
        } else {
          flushList();
          inCode = true;
        }
        i++; continue;
      }
      if (inCode) { codeBuf.push(line); i++; continue; }

      // 空行
      if (/^\s*$/.test(line)) { flushList(); i++; continue; }

      // 水平线
      if (/^---+\s*$/.test(line) || /^\*\*\*+\s*$/.test(line)) {
        flushList(); html.push('<hr>'); i++; continue;
      }

      // 标题
      var h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        flushList();
        var lvl = h[1].length;
        if (lvl > 4) lvl = 4;
        var txt = inline(h[2]);
        var id = slug(h[2]);
        html.push('<h' + lvl + ' id="' + id + '">' + txt + '</h' + lvl + '>');
        i++; continue;
      }

      // 引用
      if (/^>\s?/.test(line)) {
        flushList();
        var buf = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          buf.push(inline(lines[i].replace(/^>\s?/, '')));
          i++;
        }
        html.push('<blockquote><p>' + buf.join('<br>') + '</p></blockquote>');
        continue;
      }

      // 表格 (| a | b |)
      if (/^\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\|[\s\-:|]+\|\s*$/.test(lines[i + 1])) {
        flushList();
        var header = splitRow(line);
        i += 2; // 跳过分隔行
        var rows = [];
        while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) {
          rows.push(splitRow(lines[i]));
          i++;
        }
        var t = '<table><thead><tr>';
        header.forEach(function (c) { t += '<th>' + inline(c) + '</th>'; });
        t += '</tr></thead><tbody>';
        rows.forEach(function (r) {
          t += '<tr>';
          // 对齐: 行列数补齐
          for (var k = 0; k < header.length; k++) {
            t += '<td>' + inline(r[k] || '') + '</td>';
          }
          t += '</tr>';
        });
        t += '</tbody></table>';
        html.push(t);
        continue;
      }

      // 有序列表
      var ol = line.match(/^\d+\.\s+(.*)$/);
      if (ol) {
        if (listType !== 'ol') { flushList(); html.push('<ol>'); listType = 'ol'; }
        html.push('<li>' + inline(ol[1]) + '</li>');
        i++; continue;
      }
      // 无序列表
      var ul = line.match(/^[-*+]\s+(.*)$/);
      if (ul) {
        if (listType !== 'ul') { flushList(); html.push('<ul>'); listType = 'ul'; }
        html.push('<li>' + inline(ul[1]) + '</li>');
        i++; continue;
      }

      // 普通段落
      flushList();
      // 合并连续段落行
      var para = [line];
      while (i + 1 < lines.length && !/^\s*$/.test(lines[i + 1]) &&
        !/^(#{1,6}\s|>\s?|\||```|---|\*\*\*|[-*+]\s|\d+\.\s)/.test(lines[i + 1])) {
        i++; para.push(lines[i]);
      }
      html.push('<p>' + inline(para.join(' ')) + '</p>');
      i++;
    }
    flushList();
    if (inCode && codeBuf.length) html.push('<pre><code>' + esc(codeBuf.join('\n')) + '</code></pre>');
    return html.join('\n');
  }
  function splitRow(line) {
    return line.replace(/^\||\|$/g, '').split('|').map(function (c) { return c.trim(); });
  }
  function slug(s) {
    return 'md-' + String(s).replace(/[^\u4e00-\u9fa5a-zA-Z0-9]+/g, '-').replace(/^-|-$/g, '').toLowerCase();
  }
  // 从 Markdown 提取目录 (h2/h3)
  function buildToc(md) {
    if (!md) return [];
    var toc = [];
    var lines = String(md).split('\n');
    lines.forEach(function (l) {
      var m = l.match(/^(#{2,3})\s+(.*)$/);
      if (m) toc.push({ level: m[1].length, title: m[2].replace(/\*\*/g, ''), id: slug(m[2]) });
    });
    return toc;
  }

  /* ===================================================================
     导航 / 路由
     =================================================================== */
  function initNavigation() {
    var navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(function (item) {
      item.addEventListener('click', function (e) {
        e.preventDefault();
        var page = item.getAttribute('data-page');
        switchPage(page);
        navItems.forEach(function (n) { n.classList.remove('active'); });
        item.classList.add('active');
        // 关闭移动端侧边栏
        if (window.innerWidth <= 768) document.getElementById('sidebar').classList.remove('open');
      });
    });
    // 移动端菜单切换
    var toggle = document.getElementById('menu-toggle');
    if (toggle) {
      toggle.addEventListener('click', function () {
        document.getElementById('sidebar').classList.toggle('open');
      });
    }
    // 全局搜索
    var search = document.getElementById('global-search');
    if (search) {
      search.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') performSearch(search.value);
      });
    }
    var sbtn = document.querySelector('.search-btn');
    if (sbtn) sbtn.addEventListener('click', function () { performSearch(document.getElementById('global-search').value); });

    // 支持 hash 直达
    window.addEventListener('hashchange', function () {
      var h = (location.hash || '#dashboard').replace('#', '');
      var target = document.querySelector('.nav-item[data-page="' + h + '"]');
      if (target) target.click();
    });
  }
  function switchPage(pageName) {
    document.querySelectorAll('.page').forEach(function (p) { p.classList.remove('active'); });
    var target = document.getElementById('page-' + pageName);
    if (target) target.classList.add('active');
    document.querySelector('.pages').scrollTop = 0;
    // 按需 (重)渲染
    if (pageName === 'sectors') renderSectorsPage();
    if (pageName === 'archive') ensureArchiveToolbar();
  }
  function performSearch(term) {
    term = (term || '').trim().toLowerCase();
    if (!term) return;
    // 简易搜索: 在金股/选股/信源中匹配
    var hits = [];
    var d = appState.latestData || {};
    (d.gold_stocks || []).forEach(function (s) {
      if ((s.name + s.code + s.reason).toLowerCase().indexOf(term) !== -1)
        hits.push('金股: ' + s.name + '(' + s.code + ')');
    });
    (appState.historyData.goldStocks && appState.historyData.goldStocks.stocks || []).forEach(function (s) {
      if ((s.name + s.code).toLowerCase().indexOf(term) !== -1)
        hits.push('历史金股: ' + s.name + '(' + s.code + ')');
    });
    if (hits.length) {
      switchPage('gold-stocks');
      document.querySelector('.nav-item[data-page="gold-stocks"]').classList.add('active');
      document.querySelectorAll('.nav-item').forEach(function (n) { if (n.dataset.page !== 'gold-stocks') n.classList.remove('active'); });
      alert('搜索 "' + term + '" 命中 ' + hits.length + ' 条:\n\n' + hits.slice(0, 8).join('\n'));
    } else {
      alert('未找到与 "' + term + '" 相关的结果');
    }
  }

  /* ===================================================================
     页面 1: 今日看板
     =================================================================== */
  function renderDashboard(data) {
    // v5 三栏布局：观澜洞见 / 闲看潮涌 / 踏浪分金
    renderGuanlanInsights();
    renderMarketDashboard();
    renderGoldStocksQuick();
    // 保留原有的态势横幅等（如果对应元素仍存在）
    if (document.getElementById('dash-situation') ||
        document.getElementById('dash-battlefield') ||
        document.getElementById('dash-metrics') ||
        document.getElementById('dash-alerts')) {
      data = data || appState.latestData || buildFallbackLatest();
      renderSituationBanner(data);
      renderBattlefield(data);
      renderMetricsStrip(data);
      renderAlerts(data);
    }
  }

  /* ---- v5 栏目一：观澜洞见 ---- */
  var activeInsightPeriod = 'morning';  // 当前选中的洞见时段 (morning/noon/evening)

  function renderGuanlanInsights() {
    var container = document.getElementById('insights-content');
    if (!container) return;
    container.innerHTML = '<p class="loading-text">加载中...</p>';

    // 无指定日期时优先使用 /api/insights/latest（取最新有数据的日期）
    var date = appState.currentDate;
    var url;
    if (date) {
      url = API_BASE + '/api/insights?date=' + encodeURIComponent(date) +
            '&period=' + encodeURIComponent(activeInsightPeriod);
    } else {
      url = API_BASE + '/api/insights/latest?period=' + encodeURIComponent(activeInsightPeriod);
    }

    fetch(url + (url.indexOf('?') > -1 ? '&' : '?') + 't=' + Date.now())
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (resp) {
        var list = extractList(resp);
        if (list.length > 0) {
          container.innerHTML = list.slice(0, 8).map(function (ins) {
            var conf = String(ins.confidence || '').toLowerCase();
            var confClass = conf === 'high' ? 'insight-cat-high' :
                            conf === 'medium' ? 'insight-cat-medium' : 'insight-cat-low';
            return '<div class="insight-item">' +
              '<span class="insight-category ' + confClass + '">' + esc(ins.category || '洞见') + '</span>' +
              '<span>' + esc(ins.signal_text || '') + '</span>' +
              (ins.a_share_impact ? '<div style="color:var(--text-muted,#888);font-size:12px;margin-top:4px;">' +
                esc(ins.a_share_impact) + '</div>' : '') +
              '</div>';
          }).join('');
        } else {
          container.innerHTML = '<p class="loading-text">暂无洞见数据</p>';
        }
      })
      .catch(function () {
        container.innerHTML = '<p class="loading-text">洞见数据加载失败</p>';
      });
  }

  /* ---- v5 栏目二：闲看潮涌（市场数据仪表盘） ---- */
  function renderMarketDashboard() {
    var date = dashboardDate();

    // 指数行情（美股 / 亚太 / A股）
    fetch(API_BASE + '/api/indices?date=' + encodeURIComponent(date) + '&t=' + Date.now())
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (resp) {
        var data = extractList(resp);
        var usEl = document.getElementById('us-data');
        var asiaEl = document.getElementById('asia-data');
        var aEl = document.getElementById('ashare-data');
        if (data.length === 0) {
          if (usEl) usEl.innerHTML = '<p class="loading-text">暂无数据</p>';
          if (asiaEl) asiaEl.innerHTML = '<p class="loading-text">暂无数据</p>';
          if (aEl) aEl.innerHTML = '<p class="loading-text">暂无数据</p>';
          return;
        }
        var usNames = ['道琼斯', '纳斯达克', '标普500'];
        var asiaNames = ['恒生指数', '恒生科技', '日经225'];
        var aNames = ['上证指数', '深证成指', '创业板指', '科创50'];

        function renderList(names) {
          var html = names.map(function (n) {
            var idx = null;
            for (var i = 0; i < data.length; i++) {
              if (data[i] && data[i].name === n) { idx = data[i]; break; }
            }
            if (!idx) return '';
            var pct = parseFloat(idx.pct_chg);
            var cls = (!isNaN(pct) && pct > 0) ? 'up' : 'down';
            return '<div class="market-item"><span class="name">' + esc(n) +
              '</span><span class="val ' + cls + '">' +
              (pct > 0 ? '+' : '') + (isNaN(pct) ? '--' : pct.toFixed(2)) + '%</span></div>';
          }).join('');
          return html;
        }
        if (usEl) usEl.innerHTML = renderList(usNames) || '<p class="loading-text">暂无数据</p>';
        if (asiaEl) asiaEl.innerHTML = renderList(asiaNames) || '<p class="loading-text">暂无数据</p>';
        if (aEl) aEl.innerHTML = renderList(aNames) || '<p class="loading-text">暂无数据</p>';
      })
      .catch(function () { /* 静默失败，保留 loading 提示 */ });

    // 板块热度排行
    fetch(API_BASE + '/api/sectors?date=' + encodeURIComponent(date) + '&top=8&t=' + Date.now())
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (resp) {
        var el = document.getElementById('sector-rank');
        if (!el) return;
        var data = extractList(resp);
        if (data.length === 0) { el.innerHTML = '<p class="loading-text">暂无数据</p>'; return; }
        el.innerHTML = data.slice(0, 8).map(function (s, i) {
          var net = parseFloat(s.net_mf_amount);
          var cls = (!isNaN(net) && net > 0) ? 'up' : 'down';
          return '<div class="sector-rank-item">' +
            '<span>' + (i + 1) + '. ' + esc(s.industry || s.name || '') + '</span>' +
            '<span class="val ' + cls + '">' + (net > 0 ? '+' : '') +
            (isNaN(net) ? '--' : (net / 1e4).toFixed(1)) + '亿</span></div>';
        }).join('');
      })
      .catch(function () { /* 静默失败 */ });

    // 市场温度（沿用 latestData 的市场统计，无 API 时降级展示）
    var tg = document.getElementById('temp-gauge');
    if (tg) {
      var d = appState.latestData || {};
      var m = d.market || {};
      var items = [];
      if (m.limit_up != null) items.push({ k: '涨停', v: safe(m.limit_up, 0), c: 'up' });
      if (m.limit_down != null) items.push({ k: '跌停', v: safe(m.limit_down, 0), c: 'down' });
      if (m.volume) items.push({ k: '成交', v: esc(m.volume), c: '' });
      tg.innerHTML = items.length ? items.map(function (it) {
        return '<div class="market-item"><span class="name">' + it.k + '</span>' +
          '<span class="val ' + it.c + '">' + it.v + '</span></div>';
      }).join('') : '<div class="temp-empty">暂无温度数据</div>';
    }
  }

  /* ---- v5 栏目三：踏浪分金（金股速览） ---- */
  function renderGoldStocksQuick() {
    var container = document.getElementById('gold-stocks-quick');
    if (!container) return;
    container.innerHTML = '<p class="loading-text">加载中...</p>';

    // 无指定日期时使用 /api/gold-stocks/recent（最近5天）
    var date = appState.currentDate;
    var url;
    if (date) {
      url = API_BASE + '/api/gold-stocks?date=' + encodeURIComponent(date) + '&limit=10';
    } else {
      url = API_BASE + '/api/gold-stocks/recent?days=5&limit=10';
    }

    fetch(url + (url.indexOf('?') > -1 ? '&' : '?') + 't=' + Date.now())
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (resp) {
        var list = extractList(resp);
        if (list.length === 0) {
          container.innerHTML = '<p class="loading-text">暂无金股推荐</p>';
          return;
        }
        var html = '<table class="gold-quick-table"><thead><tr>' +
          '<th>名称</th><th>代码</th><th>评分</th><th>共振</th>' +
          '<th>维度</th><th>板块</th><th>买入区间</th><th>目标价</th>' +
          '<th>强度</th><th>入库时间</th></tr></thead><tbody>';
        list.slice(0, 10).forEach(function (g) {
          var strength = g.strength || '关注';
          var badge = strength === '重点关注' ? 'strong' : 'normal';
          html += '<tr>' +
            '<td>' + esc(g.name || '') + '</td>' +
            '<td>' + esc(g.code || '') + '</td>' +
            '<td>' + (g.score != null ? esc(g.score) : '--') + '</td>' +
            '<td>' + esc(g.verification || '') + '</td>' +
            '<td>' + esc(g.signal_source || g.reason || '') + '</td>' +
            '<td>' + esc(g.dragon_vein || '-') + '</td>' +
            '<td>' + esc(g.buy_range || '-') + '</td>' +
            '<td>' + esc(g.target_price || '-') + '</td>' +
            '<td><span class="gold-badge ' + badge + '">' + esc(strength) + '</span></td>' +
            '<td>' + esc(g.recommend_date || '') + '</td>' +
            '</tr>';
        });
        html += '</tbody></table>';
        container.innerHTML = html;
      })
      .catch(function () {
        container.innerHTML = '<p class="loading-text">金股数据加载失败</p>';
      });
  }

  // 洞见 Tab 切换（事件委托：盘前/盘中/盘后）
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (t && t.classList && t.classList.contains('insight-tab')) {
      document.querySelectorAll('.insight-tab').forEach(function (x) { x.classList.remove('active'); });
      t.classList.add('active');
      activeInsightPeriod = t.getAttribute('data-period') || 'morning';
      renderGuanlanInsights();
    }
  });

  function renderSituationBanner(data) {
    var el = document.getElementById('dash-situation');
    if (!el) return;
    var d = data || appState.latestData;
    var summary = d && d.summary ? d.summary : '市场数据加载中…';
    // 由评分决定动作色
    var score = d && d.score ? d.score : 0;
    var action = '空仓观望 · 等待介入信号';
    var cls = 'watch';
    if (score >= 85) { action = '多头主导 · 顺势参与'; cls = 'active'; }
    else if (score >= 70) { action = '结构性行情 · 半仓试探'; cls = 'active'; }
    else if (score < 55) { action = '风险偏高 · 谨慎观望'; cls = 'caution'; }
    // 高亮关键词
    var hl = esc(summary)
      .replace(/(AI算力|半导体|存储芯片|军工航天|低空经济|机器人|新能源|光模块|高潮|退烧|崛起)/g, '<span class="highlight">$1</span>');
    el.innerHTML =
      '<div class="situation-banner">' +
        '<div class="situation-text">' +
          '<div class="situation-label">当前态势 · ' + fmtDate(d && d.date) + ' ' + typeLabel(d && d.type) + '</div>' +
          '<div class="situation-headline">' + hl + '</div>' +
        '</div>' +
        '<button class="situation-action ' + cls + '">🟡 ' + action + '</button>' +
      '</div>';
  }

  function renderBattlefield(data) {
    var el = document.getElementById('dash-battlefield');
    if (!el) return;
    var d = data || appState.latestData;
    var m = (d && d.market) || {};
    var indices = m.indices || [];
    // 取前3个指数
    var top3 = indices.slice(0, 3);

    // 市场温度计
    var tempHtml = '<div class="battle-column guanlan">' +
      '<div class="battle-header guanlan"><span style="font-size:18px">🌡️</span> 市场温度计</div>' +
      '<div class="temp-board">' +
        top3.map(function (ix) {
          var cc = changeClass(ix.change);
          return '<div class="temp-index">' +
            '<div class="name">' + esc(ix.name) + '</div>' +
            '<div class="value ' + cc + '">' + esc(ix.value) + '</div>' +
            '<div class="change ' + cc + '">' + changeStr(ix.change) + '</div>' +
          '</div>';
        }).join('') +
      '</div>' +
      '<div class="temp-row">' +
        '<div class="temp-cell"><span class="label">两市成交额</span><span class="num">' + esc(m.volume || '--') + '</span></div>' +
        '<div class="temp-cell"><span class="label">涨停 / 跌停</span><span class="num"><span class="up">' + safe(m.limit_up, 0) + '</span> / <span class="down">' + safe(m.limit_down, 0) + '</span></span></div>' +
      '</div>' +
      '<div class="temp-row">' +
        '<div class="temp-cell"><span class="label">上涨家数</span><span class="num up">' + safe(m.up_count, '--') + '</span></div>' +
        '<div class="temp-cell"><span class="label">下跌家数</span><span class="num down">' + safe(m.down_count, '--') + '</span></div>' +
      '</div>' +
      buildTempGauge(d) +
      buildSectorTop(d) +
    '</div>';

    // 板块热度 Top5
    var sectorsHtml = '<div class="battle-column qingping">' +
      '<div class="battle-header qingping"><span style="font-size:18px">🔥</span> 板块热度 Top5</div>' +
      buildSectorTop5(d) +
    '</div>';

    // 金股速览
    var goldHtml = '<div class="battle-column talong">' +
      '<div class="battle-header talong"><span style="font-size:18px">🏆</span> 金股速览</div>' +
      buildGoldMini(d) +
    '</div>';

    el.innerHTML = tempHtml + sectorsHtml + goldHtml;
  }

  function buildTempGauge(d) {
    var score = (d && d.score) || 60;
    var pct = Math.max(0, Math.min(100, score));
    var txt = '🟡 微热 · 分化市', cls = 'warm';
    if (score >= 85) { txt = '🔴 过热 · 高潮市'; cls = 'hot'; }
    else if (score < 55) { txt = '🟢 偏冷 · 防御市'; cls = 'cool'; }
    return '<div class="temp-gauge">' +
      '<div class="gauge-label">报告综合评分 · ' + score + '/100</div>' +
      '<div class="gauge-bar"><div class="gauge-marker" style="left:' + pct + '%;"></div></div>' +
      '<div class="gauge-text ' + cls + '">' + txt + '</div>' +
      '<div class="temp-note">基于指数+板块+量能+金股综合</div>' +
    '</div>';
  }

  function buildSectorTop(d) {
    var sectors = ((d && d.heat && d.heat.sectors) || []).slice().sort(function (a, b) {
      return (b.current_heat || 0) - (a.current_heat || 0);
    }).slice(0, 5);
    if (!sectors.length) return '';
    var medals = ['🥇', '🥈', '🥉', '④', '⑤'];
    return '<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--border);">' +
      '<div style="font-size:11px;color:var(--text-muted);margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px;">潮汐排行 · 板块热度</div>' +
      '<div class="sector-mini-list">' +
        sectors.map(function (s, i) {
          var h = s.current_heat || 0;
          return '<div class="sector-mini-item" onclick="document.querySelector(\'.nav-item[data-page=sectors]\').click()">' +
            '<span class="name">' + medals[i] + ' ' + esc(s.name) + '</span>' +
            '<span class="flow ' + (h < 0 ? 'neg' : '') + '">' + (h >= 0 ? '+' : '') + num(h, 1) + '</span>' +
          '</div>';
        }).join('') +
      '</div></div>';
  }
  function buildSectorTop5(d) {
    var sectors = ((d && d.heat && d.heat.sectors) || []).slice().sort(function (a, b) {
      return (b.current_heat || 0) - (a.current_heat || 0);
    }).slice(0, 5);
    if (!sectors.length) return '<div class="empty-box" style="padding:24px;">暂无板块数据</div>';
    var medals = ['🥇', '🥈', '🥉', '④', '⑤'];
    return '<div class="sector-mini-list">' +
      sectors.map(function (s, i) {
        var h = s.current_heat || 0;
        var lc = s.lifecycle || {};
        return '<div class="sector-mini-item" onclick="document.querySelector(\'.nav-item[data-page=sectors]\').click()">' +
          '<span class="name">' + medals[i] + ' ' + esc(s.name) +
            ' <span class="badge ' + badgeClass(lc.state) + '" style="margin-left:6px;">' + esc(lc.state || '--') + '</span></span>' +
          '<span class="flow ' + (h < 0 ? 'neg' : '') + '">' + (h >= 0 ? '+' : '') + num(h, 1) + '</span>' +
        '</div>';
      }).join('') +
    '</div>';
  }
  function buildGoldMini(d) {
    var golds = (d && d.gold_stocks) || [];
    if (!golds.length) return '<div class="empty-box" style="padding:24px;">暂无金股推荐</div>';
    return '<div class="gold-mini-list">' +
      golds.map(function (g) {
        return '<div class="gold-mini-item">' +
          '<div class="gold-mini-head">' +
            '<div><span class="gold-mini-name">' + esc(g.name) + '</span><span class="gold-mini-code">' + esc(g.code) + '</span></div>' +
            '<div class="gold-mini-score">' + safe(g.score, '--') + '</div>' +
          '</div>' +
          '<div class="gold-mini-reason">' + esc(g.reason || '') + '</div>' +
        '</div>';
      }).join('') +
    '</div>';
  }

  function renderMetricsStrip(data) {
    var el = document.getElementById('dash-metrics');
    if (!el) return;
    var d = data || appState.latestData;
    var m = (d && d.market) || {};
    var indices = m.indices || [];
    var sh = indices[0] || {};
    var cy = indices[2] || indices[1] || {};
    var pills = [
      { v: changeStr(sh.change), l: sh.name || '上证指数', c: changeClass(sh.change) },
      { v: changeStr(cy.change), l: cy.name || '创业板指', c: changeClass(cy.change) },
      { v: esc(m.volume || '--'), l: '两市成交', c: '' },
      { v: safe(m.limit_up, '--'), l: '涨停家数', c: 'up' },
      { v: safe(m.limit_down, '--'), l: '跌停家数', c: 'down' },
      { v: ((d && d.gold_stocks) || []).length, l: '今日金股', c: 'warn' }
    ];
    el.innerHTML = pills.map(function (p) {
      return '<div class="metric-pill"><div class="value ' + p.c + '">' + p.v + '</div><div class="label">' + p.l + '</div></div>';
    }).join('');
  }

  function renderAlerts(data) {
    var el = document.getElementById('dash-alerts');
    if (!el) return;
    var d = data || appState.latestData;
    var cls = (d && d.cls) || {};
    var tele = (cls.telegraph || []).slice(0, 3);
    var items = tele.map(function (t) {
      var ic = t.important ? 'danger' : 'info';
      return '<div class="alert-item ' + ic + '">' +
        '<span class="alert-time">' + esc(t.time) + '</span>' +
        '<span class="alert-text">' + esc(t.text) + '</span>' +
      '</div>';
    }).join('');
    el.innerHTML = '<div class="alert-stream">' +
      '<div class="alert-stream-header"><span>🔔</span> 最新电报预警</div>' +
      '<div class="alert-stream-items">' + (items || '<div class="alert-text">暂无预警</div>') + '</div>' +
    '</div>';
  }

  /* ===================================================================
     页面 2: 日报归档
     =================================================================== */
  var archiveToolbarReady = false;
  function ensureArchiveToolbar() {
    if (archiveToolbarReady) {
      // 已有 toolbar, 直接渲染默认报告
      if (!appState.activeArchive) loadAndRenderArchive(appState.currentDate, appState.currentType);
      return;
    }
    renderArchiveToolbar();
    archiveToolbarReady = true;
    // 默认加载最新
    loadAndRenderArchive(appState.currentDate, appState.currentType);
  }

  function renderArchiveToolbar() {
    var el = document.getElementById('archive-toolbar');
    if (!el) return;
    var mf = appState.manifest || FALLBACK_MANIFEST;
    var dates = availableDates();
    var today = (mf.latest_date || dates[0] || '');

    // 类型 tabs
    var tabs = REPORT_TYPES.map(function (t) {
      return '<div class="type-tab" data-type="' + t.key + '">' + t.label + '</div>';
    }).join('');

    el.innerHTML =
      '<div style="display:flex;align-items:flex-start;gap:20px;flex-wrap:wrap;width:100%;">' +
        '<div class="mini-calendar" id="archive-mini-cal"></div>' +
        '<div style="flex:1;min-width:260px;">' +
          '<div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">选择日期</div>' +
          '<div class="date-nav">' +
            '<select class="date-select" id="archive-date-select">' +
              dates.map(function (d) { return '<option value="' + d + '"' + (d === today ? ' selected' : '') + '>' + fmtDate(d) + '</option>'; }).join('') +
            '</select>' +
            '<button class="date-nav-btn" id="archive-prev" title="上一日">‹</button>' +
            '<button class="date-nav-btn" id="archive-next" title="下一日">›</button>' +
          '</div>' +
          '<div style="font-size:12px;color:var(--text-muted);margin:14px 0 8px;">报告类型</div>' +
          '<div class="type-tabs" id="archive-type-tabs">' + tabs + '</div>' +
          '<div id="archive-hint" style="font-size:11px;color:var(--text-muted);margin-top:12px;"></div>' +
        '</div>' +
      '</div>';

    // 默认选中最新类型
    var defType = (mf.latest_type || 'evening');
    document.querySelectorAll('#archive-type-tabs .type-tab').forEach(function (t) {
      if (t.getAttribute('data-type') === defType) t.classList.add('active');
    });
    if (!document.querySelector('#archive-type-tabs .type-tab.active')) {
      var first = document.querySelector('#archive-type-tabs .type-tab');
      if (first) first.classList.add('active');
    }

    // 渲染迷你日历
    renderMiniCalendar(today);

    // 事件绑定
    document.getElementById('archive-date-select').addEventListener('change', function (e) {
      onArchiveDateChange(e.target.value);
    });
    document.getElementById('archive-prev').addEventListener('click', function () {
      var sel = document.getElementById('archive-date-select');
      var idx = sel.selectedIndex;
      if (idx < sel.options.length - 1) { sel.selectedIndex = idx + 1; onArchiveDateChange(sel.value); }
    });
    document.getElementById('archive-next').addEventListener('click', function () {
      var sel = document.getElementById('archive-date-select');
      var idx = sel.selectedIndex;
      if (idx > 0) { sel.selectedIndex = idx - 1; onArchiveDateChange(sel.value); }
    });
    document.querySelectorAll('#archive-type-tabs .type-tab').forEach(function (t) {
      t.addEventListener('click', function () {
        document.querySelectorAll('#archive-type-tabs .type-tab').forEach(function (x) { x.classList.remove('active'); });
        t.classList.add('active');
        onArchiveTypeChange(t.getAttribute('data-type'));
      });
    });
  }

  function availableDates() {
    var mf = appState.manifest || FALLBACK_MANIFEST;
    var set = {};
    (mf.archives || []).forEach(function (a) { set[a.date] = true; });
    var dates = Object.keys(set).sort().reverse();
    if (!dates.length && mf.latest_date) dates = [mf.latest_date];
    return dates;
  }
  function availableTypesForDate(date) {
    var mf = appState.manifest || FALLBACK_MANIFEST;
    var types = (mf.archives || []).filter(function (a) { return a.date === date; }).map(function (a) { return a.type; });
    return types;
  }

  function onArchiveDateChange(date) {
    appState.currentDate = date;
    appState.currentType = null;
    renderMiniCalendar(date);
    // 自动选该日期可用类型, 否则默认 evening
    var types = availableTypesForDate(date);
    var want = types[0] || 'evening';
    document.querySelectorAll('#archive-type-tabs .type-tab').forEach(function (t) {
      t.classList.toggle('active', t.getAttribute('data-type') === want);
    });
    loadAndRenderArchive(date, want);
  }
  function onArchiveTypeChange(type) {
    appState.currentType = type;
    var date = document.getElementById('archive-date-select').value;
    loadAndRenderArchive(date, type);
  }

  function loadAndRenderArchive(date, type) {
    var area = document.getElementById('archive-report-area');
    if (!area) return;
    date = date || (appState.manifest || FALLBACK_MANIFEST).latest_date;
    type = type || (appState.manifest || FALLBACK_MANIFEST).latest_type || 'evening';

    // 更新提示
    var hint = document.getElementById('archive-hint');
    var avail = availableTypesForDate(date);
    var hasFile = avail.indexOf(type) !== -1;

    area.innerHTML = '<div class="loading-box"><span class="spinner"></span>正在加载 ' + fmtDate(date) + ' ' + typeLabel(type) + '…</div>';

    // 如果就是最新数据且未选日期, 直接用 latestData
    if (!appState.currentDate && appState.latestData) {
      renderArchiveReport(appState.latestData, date, type, true);
      if (hint) hint.innerHTML = '当前显示: <strong>最新报告</strong>（' + fmtDate(date) + ' ' + typeLabel(type) + '）';
      return;
    }

    loadArchive(date, type).then(function (arc) {
      if (arc) {
        appState.activeArchive = arc;
        renderArchiveReport(arc, date, type, hasFile);
        if (hint) hint.innerHTML = '已加载归档: <strong>' + fmtDate(date) + ' ' + typeLabel(type) + '</strong>' + (avail.length ? '（该日可用: ' + avail.map(typeLabel).join(' / ') + '）' : '');
        // 更新各页数据 (用归档数据)
        applyArchiveToPages(arc);
      } else {
        // 无文件, 用回退 (基于 latest)
        var fb = appState.latestData || buildFallbackLatest();
        fb = JSON.parse(JSON.stringify(fb));
        fb.date = date; fb.type = type;
        renderArchiveReport(fb, date, type, false);
        if (hint) hint.innerHTML = '<span style="color:var(--accent)">未找到 ' + fmtDate(date) + ' ' + typeLabel(type) + ' 归档文件</span>，已显示参考内容。data/archive/' + date + '_' + type + '.json';
      }
    });
  }

  function renderArchiveReport(arc, date, type, real) {
    var area = document.getElementById('archive-report-area');
    if (!area) return;
    var report = arc.report || {};
    var md = report.full_md || (report.chapters ? chaptersToMd(report.chapters) : '');
    var html = renderMarkdown(md);
    var toc = buildToc(md);

    var tocHtml = '<div class="report-toc"><h4>目录</h4>';
    if (toc.length) {
      toc.forEach(function (t) {
        tocHtml += '<a href="#' + t.id + '" class="' + (t.level === 3 ? 'sub' : '') + '">' + esc(t.title) + '</a>';
      });
    } else {
      tocHtml += '<a>暂无目录</a>';
    }
    tocHtml += '</div>';

    var scoreBadge = (arc.score != null) ? '<div class="score-badge">' + arc.score + '</div>' : '';
    var metaBar = '<div class="report-meta-bar">' +
      scoreBadge +
      '<div class="meta-item">日期: <strong>' + fmtDate(date) + '</strong></div>' +
      '<div class="meta-item">类型: <strong>' + typeLabel(type) + '</strong></div>' +
      '<div class="meta-item">标题: <strong>' + esc(arc.title || '') + '</strong></div>' +
      (real ? '' : '<div class="meta-item" style="color:var(--accent)">参考数据</div>') +
    '</div>';

    area.innerHTML = '<div class="report-layout">' +
      tocHtml +
      '<div class="report-content">' + metaBar + '<div class="md" id="archive-md">' + html + '</div></div>' +
    '</div>';

    // TOC 平滑滚动
    area.querySelectorAll('.report-toc a[href^="#"]').forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        var target = document.getElementById(a.getAttribute('href').slice(1));
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
  }
  function chaptersToMd(chapters) {
    var md = '';
    (chapters || []).forEach(function (c) {
      if (c.title) md += '## ' + c.title + '\n\n';
      if (c.content) md += c.content + '\n\n';
      (c.sections || []).forEach(function (s) {
        if (s.title) md += '### ' + s.title + '\n\n';
        if (s.content) md += s.content + '\n\n';
      });
    });
    return md;
  }
  function typeLabel(t) {
    var f = REPORT_TYPES.filter(function (x) { return x.key === t; })[0];
    return f ? f.label : (t || '未知');
  }

  // 迷你日历 (归档页)
  function renderMiniCalendar(selectedDate) {
    var el = document.getElementById('archive-mini-cal');
    if (!el) return;
    var sel = selectedDate || (appState.manifest || FALLBACK_MANIFEST).latest_date;
    var dParts = String(sel).split('-');
    var year = parseInt(dParts[0], 10) || new Date().getFullYear();
    var month = parseInt(dParts[1], 10) || (new Date().getMonth() + 1);
    var dataDates = {};
    availableDates().forEach(function (d) { dataDates[d] = true; });
    var today = todayStr();

    var first = new Date(year, month - 1, 1);
    var startDay = first.getDay(); // 0=Sun
    var daysInMonth = new Date(year, month, 0).getDate();
    var dows = ['日', '一', '二', '三', '四', '五', '六'];

    var html = '<div class="mini-cal-head">' +
      '<button class="mini-cal-nav" id="mc-prev">‹</button>' +
      '<div class="mini-cal-title">' + year + '年' + month + '月</div>' +
      '<button class="mini-cal-nav" id="mc-next">›</button>' +
    '</div><div class="mini-cal-grid">' +
      dows.map(function (d) { return '<div class="mini-cal-dow">' + d + '</div>'; }).join('');

    for (var b = 0; b < startDay; b++) html += '<div class="mini-cal-day empty"></div>';
    for (var day = 1; day <= daysInMonth; day++) {
      var ds = year + '-' + String(month).padStart(2, '0') + '-' + String(day).padStart(2, '0');
      var cls = 'mini-cal-day';
      if (dataDates[ds]) cls += ' has-data';
      if (ds === today) cls += ' today';
      if (ds === sel) cls += ' selected';
      html += '<div class="' + cls + '" data-date="' + ds + '">' + day + '</div>';
    }
    html += '</div><div class="mini-cal-legend"><span class="dot"></span> 有报告的日期</div>';
    el.innerHTML = html;

    document.getElementById('mc-prev').addEventListener('click', function () {
      var m = month - 1; var y = year;
      if (m < 1) { m = 12; y--; }
      var ds = y + '-' + String(m).padStart(2, '0') + '-01';
      renderMiniCalendar(ds);
    });
    document.getElementById('mc-next').addEventListener('click', function () {
      var m = month + 1; var y = year;
      if (m > 12) { m = 1; y++; }
      var ds = y + '-' + String(m).padStart(2, '0') + '-01';
      renderMiniCalendar(ds);
    });
    el.querySelectorAll('.mini-cal-day[data-date]').forEach(function (cell) {
      cell.addEventListener('click', function () {
        var ds = cell.getAttribute('data-date');
        var sel = document.getElementById('archive-date-select');
        if (sel) {
          // 若该日期不在下拉, 仍然尝试加载
          var exists = Array.prototype.some.call(sel.options, function (o) { return o.value === ds; });
          if (!exists) {
            // 动态加入选项
            var opt = document.createElement('option');
            opt.value = ds; opt.textContent = fmtDate(ds);
            sel.appendChild(opt);
          }
          sel.value = ds;
          onArchiveDateChange(ds);
        } else {
          appState.currentDate = ds;
          loadAndRenderArchive(ds, appState.currentType || 'evening');
        }
      });
    });
  }

  // 归档数据应用到其他页面
  function applyArchiveToPages(arc) {
    // 看板用归档数据刷新
    renderDashboard(arc);
  }

  /* ===================================================================
     页面 3: 板块热度
     =================================================================== */
  function renderSectorsPage() {
    var heat = (appState.historyData.heatTracking) || buildFallbackHeat();
    // tagbar
    renderSectorTagbar(heat);
    // 图表 (折线图 / 潮汐波浪图 切换)
    var chartContainer = document.getElementById('sector-charts');
    if (window.StockCharts && chartContainer) {
      window.StockCharts.renderSectorChartsWithToggle(chartContainer, heat, appState.hiddenSectors);
    }
    // 生命周期卡片 + 表
    renderLifecycle(heat);
  }
  function renderSectorTagbar(heat) {
    var el = document.getElementById('sector-tagbar');
    if (!el) return;
    var sectors = (heat && heat.sectors) || [];
    var palette = ['#58a6ff','#f78166','#3fb950','#d29922','#bc8cff','#f778ba','#79c0ff','#ffa657','#56d4dd','#ff7b72'];
    el.innerHTML = sectors.map(function (s, i) {
      var hidden = appState.hiddenSectors.indexOf(s.name) !== -1;
      var col = palette[i % palette.length];
      return '<div class="sector-tag ' + (hidden ? 'muted' : 'active') + '" data-name="' + esc(s.name) + '" style="' + (hidden ? '' : 'border-color:' + col + ';color:' + col + ';') + '">' +
        '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + col + ';margin-right:6px;"></span>' + esc(s.name) +
      '</div>';
    }).join('');
    el.querySelectorAll('.sector-tag').forEach(function (t) {
      t.addEventListener('click', function () {
        var name = t.getAttribute('data-name');
        var idx = appState.hiddenSectors.indexOf(name);
        if (idx === -1) appState.hiddenSectors.push(name);
        else appState.hiddenSectors.splice(idx, 1);
        renderSectorsPage();
      });
    });
  }
  function renderLifecycle(heat) {
    var sectors = ((heat && heat.sectors) || []).slice().sort(function (a, b) {
      return (b.current_heat || 0) - (a.current_heat || 0);
    });
    // 卡片
    var grid = document.getElementById('lifecycle-grid');
    if (grid) {
      grid.innerHTML = sectors.map(function (s) {
        var h = s.current_heat || 0;
        var lc = s.lifecycle || {};
        return '<div class="lifecycle-card">' +
          '<div><div class="name">' + esc(s.name) + '</div>' +
            '<span class="badge ' + badgeClass(lc.state) + '" style="margin-top:4px;display:inline-block;">' + esc(lc.state || '--') + ' ' + esc(lc.trend || '') + '</span></div>' +
          '<div class="heat ' + heatClass(h) + '">' + (h >= 0 ? '+' : '') + num(h, 1) + '</div>' +
        '</div>';
      }).join('');
    }
    // 表格
    var wrap = document.getElementById('lifecycle-table-wrap');
    if (wrap) {
      var rows = sectors.map(function (s) {
        var h = s.current_heat || 0;
        var lc = s.lifecycle || {};
        return '<tr>' +
          '<td><strong>' + esc(s.name) + '</strong></td>' +
          '<td class="num ' + (h >= 0 ? 'col-up' : 'col-down') + '">' + (h >= 0 ? '+' : '') + num(h, 1) + '</td>' +
          '<td><span class="badge ' + badgeClass(lc.state) + '">' + esc(lc.state || '--') + '</span></td>' +
          '<td class="center">' + esc(lc.trend || '-') + '</td>' +
          '<td style="font-size:12px;color:var(--text-secondary);">' + esc(lc.description || '-') + '</td>' +
        '</tr>';
      }).join('');
      wrap.innerHTML = '<div class="table-scroll"><table class="data-table">' +
        '<thead><tr><th>板块</th><th class="num">当前热度</th><th>生命周期</th><th class="center">趋势</th><th>说明</th></tr></thead>' +
        '<tbody>' + (rows || '<tr><td colspan="5" class="center" style="color:var(--text-muted);padding:20px;">暂无数据</td></tr>') + '</tbody>' +
      '</table></div>';
    }
  }

  /* ===================================================================
     页面 4: 金股追踪
     =================================================================== */
  function renderGoldStocks() {
    var hist = appState.historyData.goldStocks || FALLBACK_GOLD;
    var stocks = hist.stocks || [];
    var sum = hist.summary || {};
    // 汇总卡
    var stats = document.getElementById('gold-stats');
    if (stats) {
      var winPct = (sum.win_rate != null) ? (sum.win_rate * 100).toFixed(1) + '%' : '--';
      stats.innerHTML = [
        { v: safe(sum.total, 0), l: '总推荐次数', c: '' },
        { v: winPct, l: '胜率', c: 'up' },
        { v: (sum.avg_return != null ? '+' + num(sum.avg_return, 2) + '%' : '--'), l: '平均收益', c: 'up' },
        { v: (sum.avg_max_draw != null ? num(sum.avg_max_draw, 2) + '%' : '--'), l: '平均最大回撤', c: 'down' }
      ].map(function (s) {
        return '<div class="stat-card"><div class="value ' + s.c + '">' + s.v + '</div><div class="label">' + s.l + '</div></div>';
      }).join('');
    }
    // 表
    var wrap = document.getElementById('gold-table-wrap');
    if (!wrap) return;
    var sorted = stocks.slice().sort(function (a, b) {
      return (b.max_gain || 0) - (a.max_gain || 0);
    });
    var rows = sorted.map(function (s, i) {
      return '<tr>' +
        '<td>' + (i + 1) + '</td>' +
        '<td><strong>' + esc(s.name) + '</strong> <span style="color:var(--text-muted);font-size:11px;">' + esc(s.code) + '</span></td>' +
        '<td class="center">' + fmtDate(s.first_date) + '</td>' +
        '<td class="num">' + safe(s.count, 0) + '</td>' +
        retCell(s.ret_1d) +
        retCell(s.ret_3d) +
        retCell(s.ret_5d) +
        retCell(s.max_gain) +
        retCell(s.max_draw) +
        '<td class="gold-reason-cell">' + esc(s.reason || '') + '</td>' +
      '</tr>';
    }).join('');
    wrap.innerHTML = '<div class="table-scroll"><table class="data-table">' +
      '<thead><tr>' +
        '<th class="center">#</th>' +
        '<th>股票名称/代码</th>' +
        '<th class="center">首次推荐</th>' +
        '<th class="num">推荐次数</th>' +
        '<th class="num">1日收益</th>' +
        '<th class="num">3日收益</th>' +
        '<th class="num">5日收益</th>' +
        '<th class="num">最大收益</th>' +
        '<th class="num">最大回撤</th>' +
        '<th>推荐理由</th>' +
      '</tr></thead>' +
      '<tbody>' + (rows || '<tr><td colspan="10" class="center" style="color:var(--text-muted);padding:20px;">暂无金股历史数据</td></tr>') + '</tbody>' +
    '</table></div>';
  }
  function retCell(v) {
    if (v === null || v === undefined || v === '') return '<td class="num">--</td>';
    var n = parseFloat(v);
    var c = n > 0 ? 'col-up' : (n < 0 ? 'col-down' : '');
    var sign = n > 0 ? '+' : '';
    return '<td class="num ' + c + '">' + sign + num(n, 2) + '%</td>';
  }

  /* ===================================================================
     页面 5: 财联社信源
     =================================================================== */
  function renderClsSource() {
    var d = appState.latestData || buildFallbackLatest();
    var cls = d.cls || FALLBACK_CLS;
    // VIP 文章
    var vip = document.getElementById('cls-vip');
    if (vip) {
      var arts = cls.vip_articles || [];
      vip.innerHTML = arts.length ? arts.map(function (a) {
        return '<div class="vip-article-card">' +
          '<div class="vip-article-head">' +
            '<div class="vip-article-title">' + esc(a.title) + '</div>' +
            '<span class="vip-article-type">' + esc(a.type) + '</span>' +
          '</div>' +
          '<div class="vip-article-summary">' + esc(a.summary || '') + '</div>' +
          '<div class="vip-article-stocks">' +
            (a.stocks || []).map(function (s) { return '<span class="stock-chip">' + esc(s) + '</span>'; }).join('') +
          '</div>' +
        '</div>';
      }).join('') : '<div class="empty-box">暂无VIP文章</div>';
    }
    // 电报
    var tg = document.getElementById('cls-telegraph');
    if (tg) {
      var tele = cls.telegraph || [];
      tg.innerHTML = tele.length ? tele.map(function (t) {
        return '<div class="telegraph-item' + (t.important ? ' important' : '') + '">' +
          '<div class="telegraph-time">' + esc(t.time) + '</div>' +
          '<div class="telegraph-text">' + esc(t.text) + '</div>' +
        '</div>';
      }).join('') : '<div class="empty-box">暂无电报</div>';
    }
    // 发现表
    var dw = document.getElementById('cls-discovery-wrap');
    if (dw) {
      var disc = cls.discovery || [];
      var rows = disc.map(function (s, i) {
        var match = s.match || 0;
        var pct = Math.min(100, match * 5);
        return '<tr>' +
          '<td class="center">' + (i + 1) + '</td>' +
          '<td><strong>' + esc(s.name) + '</strong> <span style="color:var(--text-muted);font-size:11px;">' + esc(s.code) + '</span></td>' +
          '<td>' + esc(s.board || '--') + '</td>' +
          '<td>' + esc(s.industry || '--') + '</td>' +
          '<td><div class="match-cell"><div class="match-bar"><div class="fill" style="width:' + pct + '%;"></div></div>' + match + '</div></td>' +
          '<td style="font-size:12px;color:var(--text-secondary);max-width:260px;">' + esc(s.article || '') + '</td>' +
        '</tr>';
      }).join('');
      dw.innerHTML = '<div class="table-scroll"><table class="data-table discovery-table">' +
        '<thead><tr><th class="center">#</th><th>股票名称/代码</th><th>板块</th><th>行业</th><th>匹配度</th><th>来源文章</th></tr></thead>' +
        '<tbody>' + (rows || '<tr><td colspan="6" class="center" style="color:var(--text-muted);padding:20px;">暂无发现股票</td></tr>') + '</tbody>' +
      '</table></div>';
    }
  }

  /* ===================================================================
     页面 6: 钱三强选股
     =================================================================== */
  function renderQsq() {
    var d = appState.latestData || buildFallbackLatest();
    var qsq = d.qsq || FALLBACK_QSQ;
    var sum = qsq.summary || {};
    // 汇总
    var stats = document.getElementById('qsq-stats');
    if (stats) {
      stats.innerHTML = [
        { v: safe(sum.total, 0), l: '全市场股票', c: '' },
        { v: safe(sum.pass1, 0), l: '第一强通过', c: 'up' },
        { v: safe(sum.pass2, 0), l: '第二强通过', c: 'up' },
        { v: safe(sum.pass3, 0), l: '第三强通过', c: 'up' },
        { v: safe(sum.pass_all, 0), l: '三强共振', c: 'warn' }
      ].map(function (s) {
        return '<div class="stat-card"><div class="value ' + s.c + '">' + s.v + '</div><div class="label">' + s.l + '</div></div>';
      }).join('');
    }
    // 选股卡
    var cards = document.getElementById('qsq-cards');
    if (cards) {
      var stocks = qsq.selected_stocks || [];
      cards.innerHTML = stocks.length ? stocks.map(function (s) {
        var code = String(s.ts_code || '').replace(/\.(SZ|SH|BJ)$/, '');
        var pct = s.pct_chg || 0;
        var total = Math.round(((s.jigou || 0) / 30000 + (s.youzi || 0) / 15000 + (s.ema55_angle || 0) / 60) * 100) / 10;
        return '<div class="qsq-card">' +
          '<div class="qsq-header">' +
            '<div><div class="qsq-name">' + esc(s.name) + '</div><div class="qsq-code">' + esc(code) + ' · ' + esc(s.industry || '') + '</div></div>' +
            '<div class="qsq-score"><div class="num">' + num(s.close, 2) + '</div><div class="label">收盘价</div></div>' +
          '</div>' +
          '<div class="qsq-scores-grid">' +
            '<div class="qsq-score-box"><div class="lbl">第一强·机构</div><div class="val col-up">' + num(s.jigou, 0) + '</div></div>' +
            '<div class="qsq-score-box"><div class="lbl">第二强·游资</div><div class="val col-up">' + num(s.youzi, 0) + '</div></div>' +
            '<div class="qsq-score-box"><div class="lbl">第三强·EMA55</div><div class="val">' + num(s.ema55_angle, 1) + '°</div></div>' +
          '</div>' +
          '<div class="score-total" style="text-align:center;padding-top:10px;border-top:1px solid var(--border);font-size:13px;">' +
            '涨跌幅 <span class="' + (pct >= 0 ? 'col-up' : 'col-down') + '" style="font-weight:700;">' + (pct >= 0 ? '+' : '') + num(pct, 2) + '%</span> · ' +
            '换手 <span style="color:var(--text-primary);">' + num(s.turnover_rate, 2) + '%</span>' +
          '</div>' +
          '<div class="qsq-resonance">三强共振：机构资金 + 游资资金 + EMA55趋势角度同时满足，高共振标的。</div>' +
        '</div>';
      }).join('') : '<div class="empty-box">暂无选股结果</div>';
    }
    // 历史表现
    var hw = document.getElementById('qsq-history-wrap');
    if (hw) {
      var hist = qsq.history || [];
      var rows = hist.map(function (h) {
        var wr = h.stocks ? (h.win / h.stocks * 100).toFixed(1) + '%' : '--';
        return '<tr>' +
          '<td class="center">' + fmtDate(h.date) + '</td>' +
          '<td class="num">' + safe(h.stocks, 0) + '</td>' +
          '<td class="num">' + safe(h.win, 0) + '</td>' +
          '<td class="num">' + wr + '</td>' +
          '<td class="num ' + (h.avg_ret >= 0 ? 'col-up' : 'col-down') + '">' + (h.avg_ret >= 0 ? '+' : '') + num(h.avg_ret, 2) + '%</td>' +
        '</tr>';
      }).join('');
      hw.innerHTML = '<div class="table-scroll"><table class="data-table">' +
        '<thead><tr><th class="center">日期</th><th class="num">选股数</th><th class="num">盈利数</th><th class="num">胜率</th><th class="num">平均收益</th></tr></thead>' +
        '<tbody>' + (rows || '<tr><td colspan="5" class="center" style="color:var(--text-muted);padding:20px;">暂无历史数据</td></tr>') + '</tbody>' +
      '</table></div>';
    }
  }

  /* ===================================================================
     页面 7: 投资日历
     =================================================================== */
  function renderCalendarPage() {
    var d = appState.latestData || buildFallbackLatest();
    var cal = d.calendar || FALLBACK_CALENDAR;
    var events = cal.events || [];
    renderCalMonth(events);
    renderCalList(events);
  }
  function renderCalMonth(events) {
    var el = document.getElementById('cal-month');
    if (!el) return;
    var evMap = {};
    events.forEach(function (e) { evMap[e.date] = e; });
    var today = todayStr();
    var now = new Date();
    var year = appState.calCursor ? appState.calCursor.year : now.getFullYear();
    var month = appState.calCursor ? appState.calCursor.month : (now.getMonth() + 1);

    var first = new Date(year, month - 1, 1);
    var startDay = first.getDay();
    var daysInMonth = new Date(year, month, 0).getDate();
    var dows = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

    var html = '<div class="cal-month-head">' +
      '<button class="date-nav-btn" id="cm-prev">‹</button>' +
      '<div class="cal-month-title">' + year + '年 ' + month + '月</div>' +
      '<button class="date-nav-btn" id="cm-next">›</button>' +
    '</div><div class="cal-month-grid">' +
      dows.map(function (d) { return '<div class="cal-dow">' + d + '</div>'; }).join('');

    for (var b = 0; b < startDay; b++) html += '<div class="cal-cell empty"></div>';
    for (var day = 1; day <= daysInMonth; day++) {
      var ds = year + '-' + String(month).padStart(2, '0') + '-' + String(day).padStart(2, '0');
      var ev = evMap[ds];
      var cls = 'cal-cell';
      var mini = '';
      if (ev) {
        cls += ' has-event';
        if (ev.hot) cls += ' hot';
        mini = '<div class="cal-event-mini">' + esc(ev.title) + '</div>';
      }
      if (ds === today) cls += ' today';
      html += '<div class="' + cls + '" data-date="' + ds + '"><div class="date-num">' + day + '</div>' + mini + '</div>';
    }
    html += '</div>';
    el.innerHTML = html;

    document.getElementById('cm-prev').addEventListener('click', function () {
      var m = month - 1; var y = year;
      if (m < 1) { m = 12; y--; }
      appState.calCursor = { year: y, month: m };
      renderCalMonth(events);
    });
    document.getElementById('cm-next').addEventListener('click', function () {
      var m = month + 1; var y = year;
      if (m > 12) { m = 1; y++; }
      appState.calCursor = { year: y, month: m };
      renderCalMonth(events);
    });
    el.querySelectorAll('.cal-cell[data-date]').forEach(function (cell) {
      cell.addEventListener('click', function () {
        var ds = cell.getAttribute('data-date');
        var ev = evMap[ds];
        if (ev) {
          var list = document.getElementById('cal-list');
          if (list) list.scrollIntoView({ behavior: 'smooth', block: 'start' });
          // 闪烁高亮
          cell.style.boxShadow = '0 0 12px var(--accent)';
          setTimeout(function () { cell.style.boxShadow = ''; }, 1200);
        }
      });
    });
  }
  function renderCalList(events) {
    var el = document.getElementById('cal-list');
    if (!el) return;
    var sorted = events.slice().sort(function (a, b) {
      return String(a.date).replace(/-/g, '') - String(b.date).replace(/-/g, '');
    });
    el.innerHTML = sorted.length ? sorted.map(function (e) {
      var parts = String(e.date).split('-');
      var month = parts[1], day = parts[2];
      var stocksHtml = (e.stocks || []).map(function (s) { return '<span class="stock-chip">' + esc(s) + '</span>'; }).join('');
      return '<div class="calendar-item' + (e.hot ? ' hot-event' : '') + '">' +
        '<div class="calendar-date">' +
          '<div class="month">' + month + '月</div>' +
          '<div class="day">' + parseInt(day, 10) + '</div>' +
          '<div class="weekday">' + esc(e.weekday || '') + '</div>' +
        '</div>' +
        '<div class="calendar-content">' +
          '<div class="event-title">' + esc(e.title) + '</div>' +
          '<div class="event-meta">' +
            '<span class="event-tag sector">' + esc(e.sector || '') + '</span>' +
            (e.hot ? '<span class="event-tag hot">热门</span>' : '') +
            '<span class="event-tag">' + fmtDate(e.date) + '</span>' +
          '</div>' +
          '<div class="event-desc">' + esc(e.desc || '') + '</div>' +
          (stocksHtml ? '<div class="stocks-row">' + stocksHtml + '</div>' : '') +
        '</div>' +
      '</div>';
    }).join('') : '<div class="empty-box">暂无日历事件</div>';
  }

  /* ===================================================================
     辅助: 时间 / 市场状态
     =================================================================== */
  function todayStr() {
    var n = new Date();
    return n.getFullYear() + '-' + String(n.getMonth() + 1).padStart(2, '0') + '-' + String(n.getDate()).padStart(2, '0');
  }
  function updateLastUpdate() {
    var el = document.getElementById('last-update');
    if (!el) return;
    var t = (appState.manifest && appState.manifest.updated_at) || (appState.latestData && appState.latestData.date);
    el.textContent = t || new Date().toLocaleString('zh-CN', { hour12: false });
  }
  function updateMarketStatus() {
    var ind = document.getElementById('market-indicator');
    var txt = document.getElementById('market-state-text');
    if (!ind || !txt) return;
    var now = new Date();
    var h = now.getHours();
    var m = now.getMinutes();
    var mins = h * 60 + m;
    var day = now.getDay(); // 0=Sun
    var isWeekday = day >= 1 && day <= 5;
    // A股: 9:30-11:30, 13:00-15:00
    var inSession = isWeekday && ((mins >= 570 && mins <= 690) || (mins >= 780 && mins <= 900));
    if (inSession) {
      ind.classList.remove('closed');
      txt.textContent = '市场交易中 · 实时数据';
    } else {
      ind.classList.add('closed');
      txt.textContent = '市场休市 · 显示最新归档数据';
    }
  }

  /* ===================================================================
     全量渲染
     =================================================================== */
  function renderAllPages() {
    renderDashboard(appState.latestData);
    renderGoldStocks();
    renderClsSource();
    renderQsq();
    renderCalendarPage();
    // sectors / archive 按需渲染
  }

  /* ===================================================================
     初始化
     =================================================================== */
  document.addEventListener('DOMContentLoaded', function () {
    initNavigation();
    updateMarketStatus();
    // v4: 静态优先（GitHub Pages 可用）→ 异步检测 API（仅增强盘中刷新）
    loadStaticData().then(function () {
      renderAllPages();
      updateLastUpdate();
      updateMarketStatus();
      console.log('[App] 静态数据加载完成', {
        manifest: !!appState.manifest,
        latest: !!appState.latestData
      });
      // 异步检测 API（不阻塞渲染，仅用于盘中自动刷新增强）
      detectAPI().then(function (apiOK) {
        if (apiOK) {
          console.log('[App] API 可用，启用盘中自动刷新增强');
          startAutoRefresh();
        }
      });
    }).catch(function (e) {
      console.error('[App] 初始化失败', e);
      appState.manifest = FALLBACK_MANIFEST;
      appState.latestData = buildFallbackLatest();
      appState.historyData = { goldStocks: FALLBACK_GOLD, heatTracking: buildFallbackHeat() };
      renderAllPages();
      updateLastUpdate();
    });
  });

  // v4: 静态数据加载（GitHub Pages 模式）
  function loadStaticData() {
    return Promise.all([loadManifest(), loadLatest(), loadHistory()])
      .then(function (results) {
        appState.manifest = results[0];
        appState.latestData = results[1];
        appState.historyData = results[2];
      });
  }
})();
