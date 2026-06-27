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
   * @param {HTMLElement} container - 图表容器(可选, 缺省回退到 #sector-charts)
   * @param {Object} heatData  - { date_labels:[], sectors:[{name, heat_series, capital_series, limit_series, current_heat, lifecycle}] }
   * @param {Array}  hiddenSet - 被隐藏的板块名称列表
   */
  function renderSectorCharts(container, heatData, hiddenSet) {
    disposeAll();
    // 兼容: 未传入 container 或传入的并非 DOM 元素时, 回退到 #sector-charts
    if (!container || typeof container.appendChild !== 'function') {
      container = document.getElementById('sector-charts');
    }
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

  /**
   * 渲染潮汐波浪式可视化 (面积图模拟潮汐波浪)
   * 参考: Guanlan 项目 OceanWaveChart 设计
   * 正热度暖色(涨潮), 负热度冷色(退潮), 以平滑面积图叠加形成潮汐效果
   * @param {HTMLElement} container - 图表容器
   * @param {Object} heatData - { date_labels:[], sectors:[{name, heat_series, current_heat, lifecycle}] }
   * @returns {Object} echarts 实例
   */
  function renderWaveChart(container, heatData) {
    if (!container || !heatData || !heatData.sectors) return;

    // 容器隐藏(display:none)时 echarts 取到 0 尺寸, 这里兜底给一个高度
    if (!container.style.height && !container.offsetHeight) {
      container.style.height = '460px';
    }

    var chart = echarts.init(container, null, { renderer: 'svg' });

    // 构建波浪式面积图series
    var series = heatData.sectors.map(function (sec, i) {
      var heatData_series = sec.heat_series || [];
      // 确定颜色: 正热度暖色, 负热度冷色
      var currentHeat = sec.current_heat || 0;
      var color = currentHeat > 50 ? '#ff6b6b' :
                  currentHeat > 0 ? '#ffa500' :
                  currentHeat > -30 ? '#4ecdc4' : '#45b7d1';

      return {
        name: sec.name,
        type: 'line',
        data: heatData_series,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 0 },
        areaStyle: {
          opacity: 0.3,
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: color + 'CC' },
              { offset: 1, color: color + '11' }
            ]
          }
        },
        emphasis: {
          focus: 'series',
          lineStyle: { width: 2, opacity: 1 },
          areaStyle: { opacity: 0.5 }
        }
      };
    });

    var option = {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        formatter: function (params) {
          var html = params[0].axisValueLabel + '<br/>';
          params.sort(function (a, b) { return b.value - a.value; });
          params.forEach(function (p) {
            var lifecycle = '';
            var sec = heatData.sectors.find(function (s) { return s.name === p.seriesName; });
            if (sec && sec.lifecycle) {
              lifecycle = ' [' + (sec.lifecycle.state || '') + ']';
            }
            html += p.marker + ' ' + p.seriesName + ': ' +
                    (p.value > 0 ? '+' : '') + p.value + lifecycle + '<br/>';
          });
          return html;
        }
      },
      legend: {
        data: heatData.sectors.map(function (s) { return s.name; }),
        textStyle: { color: '#aaa', fontSize: 11 },
        type: 'scroll',
        bottom: 0
      },
      grid: { left: '3%', right: '4%', bottom: '15%', top: '5%', containLabel: true },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: heatData.date_labels || [],
        axisLabel: { color: '#666', fontSize: 11 }
      },
      yAxis: {
        type: 'value',
        min: -100,
        max: 100,
        axisLabel: { color: '#666' },
        splitLine: { lineStyle: { color: '#222' } }
      },
      series: series
    };

    chart.setOption(option);
    // 纳入统一生命周期管理, 便于 resize / dispose
    instances.push(chart);
    return chart;
  }

  /**
   * 渲染板块图表并附带 [折线图 / 潮汐波浪图] 切换按钮
   * @param {HTMLElement} container - 外层容器(通常为 #sector-charts)
   * @param {Object} heatData - 板块热度数据
   * @param {Array} hiddenSectors - 被隐藏的板块名称列表
   */
  function renderSectorChartsWithToggle(container, heatData, hiddenSectors) {
    if (!container) return;
    // 渲染切换按钮
    var toggleHtml = '<div class="chart-toggle-bar">' +
      '<button class="chart-toggle-btn active" data-mode="lines">折线图</button>' +
      '<button class="chart-toggle-btn" data-mode="wave">潮汐波浪图</button>' +
      '</div>' +
      '<div id="chart-lines-mode"></div>' +
      '<div id="chart-wave-mode" style="display:none;height:460px;"></div>';
    container.innerHTML = toggleHtml;

    // 渲染折线图模式(原有逻辑)
    var linesContainer = document.getElementById('chart-lines-mode');
    if (linesContainer) {
      renderSectorCharts(linesContainer, heatData, hiddenSectors);
    }

    // 渲染波浪图模式
    var waveContainer = document.getElementById('chart-wave-mode');
    var waveChart = null;
    if (waveContainer) {
      waveChart = renderWaveChart(waveContainer, heatData);
    }

    // 切换逻辑
    container.querySelectorAll('.chart-toggle-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        container.querySelectorAll('.chart-toggle-btn').forEach(function (b) {
          b.classList.remove('active');
        });
        btn.classList.add('active');
        var mode = btn.getAttribute('data-mode');
        var linesEl = document.getElementById('chart-lines-mode');
        var waveEl = document.getElementById('chart-wave-mode');
        if (mode === 'wave') {
          if (linesEl) linesEl.style.display = 'none';
          if (waveEl) waveEl.style.display = 'block';
          // 波浪图初始化时容器处于隐藏状态, 切换为可见后需 resize 以适配真实尺寸
          if (waveChart) { try { waveChart.resize(); } catch (e) {} }
        } else {
          if (linesEl) linesEl.style.display = 'block';
          if (waveEl) waveEl.style.display = 'none';
        }
      });
    });
  }

  // 响应式 resize
  window.addEventListener('resize', function () {
    instances.forEach(function (c) { try { c.resize(); } catch (e) {} });
  });

  // 暴露接口
  window.StockCharts = {
    renderSectorCharts: renderSectorCharts,
    renderWaveChart: renderWaveChart,
    renderSectorChartsWithToggle: renderSectorChartsWithToggle,
    disposeAll: disposeAll
  };
})();
