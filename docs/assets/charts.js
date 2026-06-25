/* ===================================================================
   板块热度图表 (ECharts)
   - 热度对比曲线 (Y: -100 ~ +100)
   - 资金流向 (亿元, 红=流入/正, 绿=流出/负)
   - 涨停数量
   A股约定: 红涨绿跌
   =================================================================== */
(function () {
  'use strict';

  // 10色调色板 (与板块一一对应)
  var PALETTE = [
    '#58a6ff', '#f78166', '#3fb950', '#d29922', '#bc8cff',
    '#f778ba', '#79c0ff', '#ffa657', '#56d4dd', '#ff7b72'
  ];

  // 读取 CSS 变量
  function cssVar(name) {
    var s = getComputedStyle(document.documentElement);
    return (s.getPropertyValue(name) || '').trim();
  }
  function theme() {
    return {
      ink: cssVar('--text-primary') || '#f8fafc',
      muted: cssVar('--text-secondary') || '#94a3b8',
      dim: cssVar('--text-muted') || '#64748b',
      rule: cssVar('--border') || '#1e293b',
      bgCard: cssVar('--bg-card') || '#111827',
      accent: cssVar('--accent') || '#f59e0b',
      up: cssVar('--up') || '#ef4444',
      down: cssVar('--down') || '#22c55e'
    };
  }

  // 缓存 echarts 实例, 便于 resize / dispose
  var instances = [];

  function disposeAll() {
    instances.forEach(function (c) { try { c.dispose(); } catch (e) {} });
    instances = [];
  }

  function makeAxisLine(t) { return { lineStyle: { color: t.rule } }; }
  function makeSplitLine(t) { return { lineStyle: { color: t.rule, type: 'dashed', opacity: 0.3 } }; }

  // 通用图例 + 网格 + 坐标轴
  function baseOption(t, sectors, dateLabels) {
    return {
      color: PALETTE,
      animation: false,
      tooltip: {
        trigger: 'axis',
        appendToBody: true,
        backgroundColor: t.bgCard,
        borderColor: t.rule,
        textStyle: { color: t.ink },
        axisPointer: { type: 'line', lineStyle: { color: t.accent, type: 'dashed' } }
      },
      legend: {
        data: sectors.map(function (s) { return s.name; }),
        top: 0,
        textStyle: { color: t.muted, fontSize: 11 },
        type: 'scroll',
        inactiveColor: t.dim
      },
      grid: { top: 50, left: 56, right: 24, bottom: 44 },
      xAxis: {
        type: 'category',
        data: dateLabels,
        boundaryGap: false,
        axisLine: makeAxisLine(t),
        axisTick: { show: false },
        axisLabel: { color: t.muted, fontSize: 10, rotate: 30 }
      }
    };
  }

  // 构建折线 series (过滤隐藏板块)
  function buildSeries(sectors, hiddenSet, valueKey) {
    return sectors.map(function (sec, i) {
      var hidden = hiddenSet && hiddenSet.indexOf(sec.name) !== -1;
      return {
        name: sec.name,
        type: 'line',
        data: sec[valueKey] || [],
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        showSymbol: false,
        lineStyle: { width: 2 },
        itemStyle: { color: PALETTE[i % PALETTE.length] },
        emphasis: { focus: 'series' },
        // 隐藏的板块淡化处理
        opacity: hidden ? 0 : 1
      };
    });
  }

  // 渲染单个图表卡片
  function renderChartCard(container, id, title, desc, height) {
    var card = document.createElement('div');
    card.className = 'chart-card';
    card.innerHTML =
      '<div class="chart-card-head">' +
        '<div class="chart-card-title">' + title + '</div>' +
      '</div>' +
      '<div class="chart-card-desc">' + desc + '</div>' +
      '<div class="chart-area" id="' + id + '" style="height:' + (height || 380) + 'px;"></div>';
    container.appendChild(card);
    return document.getElementById(id);
  }

  /**
   * 渲染板块热度三图
   * @param {Object} heatData  - { date_labels:[], sectors:[{name, heat_series, capital_series, limit_series, current_heat, lifecycle}] }
   * @param {Array}  hiddenSet - 被隐藏的板块名称列表
   */
  function renderSectorCharts(heatData, hiddenSet) {
    disposeAll();
    var container = document.getElementById('sector-charts');
    if (!container) return;
    container.innerHTML = '';

    if (!heatData || !heatData.sectors || !heatData.sectors.length) {
      container.innerHTML = '<div class="empty-box">暂无板块热度数据</div>';
      return;
    }
    hiddenSet = hiddenSet || [];
    var t = theme();
    var dateLabels = heatData.date_labels || [];
    var sectors = heatData.sectors;

    // 仅渲染存在的数据系列
    var hasHeat = sectors.some(function (s) { return s.heat_series && s.heat_series.length; });
    var hasCapital = sectors.some(function (s) { return s.capital_series && s.capital_series.length; });
    var hasLimit = sectors.some(function (s) { return s.limit_series && s.limit_series.length; });

    // 图1: 热度对比曲线
    if (hasHeat) {
      var el1 = renderChartCard(container, 'chart-heat',
        '图1 · 板块热度对比曲线',
        '热度 = 0.6×资金流向标准化 + 0.4×涨停密度标准化 | Y轴: -100(资金流出) ~ +100(资金流入) | 正值=红(热), 负值=绿(冷)');
      var c1 = echarts.init(el1, null, { renderer: 'svg' });
      var opt1 = baseOption(t, sectors, dateLabels);
      opt1.yAxis = {
        type: 'value', min: -100, max: 100, name: '热度',
        nameTextStyle: { color: t.muted },
        axisLine: makeAxisLine(t),
        axisTick: { show: false },
        splitLine: makeSplitLine(t),
        axisLabel: {
          color: t.muted,
          formatter: function (v) {
            return '<span style="color:' + (v >= 0 ? t.up : t.down) + '">' + v + '</span>';
          }
        }
      };
      opt1.series = buildSeries(sectors, hiddenSet, 'heat_series');
      opt1.tooltip.valueFormatter = function (v) { return (v >= 0 ? '+' : '') + Number(v).toFixed(1); };
      c1.setOption(opt1);
      instances.push(c1);
    }

    // 图2: 资金流向 (转亿元, 正=红/负=绿)
    if (hasCapital) {
      var el2 = renderChartCard(container, 'chart-capital',
        '图2 · 板块主力资金流向',
        '正值=资金净流入(红), 负值=资金净流出(绿) | 单位: 亿元 | 反映主力资金在各板块间的迁移轨迹');
      var c2 = echarts.init(el2, null, { renderer: 'svg' });
      var opt2 = baseOption(t, sectors, dateLabels);
      opt2.grid.left = 64;
      opt2.yAxis = {
        type: 'value', name: '亿元',
        nameTextStyle: { color: t.muted },
        axisLine: makeAxisLine(t),
        axisTick: { show: false },
        splitLine: makeSplitLine(t),
        axisLabel: {
          color: t.muted,
          formatter: function (v) { return (v / 10000).toFixed(0); }
        }
      };
      opt2.series = buildSeries(sectors, hiddenSet, 'capital_series');
      opt2.tooltip.valueFormatter = function (v) {
        var yi = (v / 10000);
        var sign = yi >= 0 ? '+' : '';
        return sign + yi.toFixed(2) + ' 亿';
      };
      c2.setOption(opt2);
      instances.push(c2);
    }

    // 图3: 涨停数量
    if (hasLimit) {
      var el3 = renderChartCard(container, 'chart-limit',
        '图3 · 板块涨停个股数量',
        '涨停判定: 主板涨跌幅≥9.8%, 科创板/创业板≥19.5% | 涨停密度反映市场情绪');
      var c3 = echarts.init(el3, null, { renderer: 'svg' });
      var opt3 = baseOption(t, sectors, dateLabels);
      opt3.yAxis = {
        type: 'value', name: '涨停数',
        nameTextStyle: { color: t.muted },
        axisLine: makeAxisLine(t),
        axisTick: { show: false },
        splitLine: makeSplitLine(t),
        axisLabel: { color: t.muted }
      };
      opt3.series = buildSeries(sectors, hiddenSet, 'limit_series');
      opt3.tooltip.valueFormatter = function (v) { return v + ' 只'; };
      c3.setOption(opt3);
      instances.push(c3);
    }
  }

  // 响应式 resize
  window.addEventListener('resize', function () {
    instances.forEach(function (c) { try { c.resize(); } catch (e) {} });
  });

  // 暴露接口
  window.StockCharts = {
    renderSectorCharts: renderSectorCharts,
    disposeAll: disposeAll
  };
})();
