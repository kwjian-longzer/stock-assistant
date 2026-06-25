// Sector Heat Tracker - ECharts Charts
(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();
  var green = style.getPropertyValue('--green').trim();
  var red = style.getPropertyValue('--red').trim();
  var yellow = style.getPropertyValue('--yellow').trim();

  // 10-color palette for 10 sectors
  var palette = [
    '#58a6ff', // AI算力 - blue
    '#f78166', // 半导体芯片 - orange
    '#3fb950', // 消费电子 - green
    '#d29922', // 新能源 - yellow
    '#bc8cff', // 机器人 - purple
    '#f778ba', // 低空经济 - pink
    '#79c0ff', // 医药生物 - light blue
    '#ffa657', // 军工航天 - amber
    '#56d4dd', // 汽车智驾 - cyan
    '#ff7b72', // 金融科技 - salmon
  ];

  // Load heat data (inline from heat_data.js)
  var heatData = (typeof HEAT_DATA !== 'undefined') ? HEAT_DATA : {};

  var dateLabels = heatData.date_labels || [];
  var sectors = heatData.sectors || [];

  // Build series for each chart
  function buildSeries(valueKey) {
    return sectors.map(function(sec, i) {
      return {
        name: sec.name,
        type: 'line',
        data: sec[valueKey],
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { width: 2 },
        itemStyle: { color: palette[i] },
        emphasis: { focus: 'series' },
      };
    });
  }

  // Chart 1: Heat Comparison
  var chart1 = echarts.init(document.getElementById('chart-heat'), null, { renderer: 'svg' });
  chart1.setOption({
    color: palette,
    animation: false,
    tooltip: {
      trigger: 'axis',
      appendToBody: true,
      backgroundColor: bg2,
      borderColor: rule,
      textStyle: { color: ink },
    },
    legend: {
      data: sectors.map(function(s) { return s.name; }),
      top: 0,
      textStyle: { color: muted, fontSize: 11 },
      type: 'scroll',
    },
    grid: { top: 50, left: 50, right: 30, bottom: 40 },
    xAxis: {
      type: 'category',
      data: dateLabels,
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: muted, fontSize: 10, rotate: 30 },
    },
    yAxis: {
      type: 'value',
      min: -100,
      max: 100,
      name: '热度',
      nameTextStyle: { color: muted },
      axisLine: { lineStyle: { color: rule } },
      splitLine: { lineStyle: { color: rule, type: 'dashed', opacity: 0.3 } },
      axisLabel: { color: muted, formatter: '{value}' },
    },
    series: buildSeries('heat_series'),
  });
  window.addEventListener('resize', function() { chart1.resize(); });

  // Chart 2: Capital Flow (convert to 亿元)
  var chart2 = echarts.init(document.getElementById('chart-capital'), null, { renderer: 'svg' });
  chart2.setOption({
    color: palette,
    animation: false,
    tooltip: {
      trigger: 'axis',
      appendToBody: true,
      backgroundColor: bg2,
      borderColor: rule,
      textStyle: { color: ink },
      valueFormatter: function(val) { return (val / 10000).toFixed(2) + ' 亿'; },
    },
    legend: {
      data: sectors.map(function(s) { return s.name; }),
      top: 0,
      textStyle: { color: muted, fontSize: 11 },
      type: 'scroll',
    },
    grid: { top: 50, left: 60, right: 30, bottom: 40 },
    xAxis: {
      type: 'category',
      data: dateLabels,
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: muted, fontSize: 10, rotate: 30 },
    },
    yAxis: {
      type: 'value',
      name: '亿元',
      nameTextStyle: { color: muted },
      axisLine: { lineStyle: { color: rule } },
      splitLine: { lineStyle: { color: rule, type: 'dashed', opacity: 0.3 } },
      axisLabel: {
        color: muted,
        formatter: function(val) { return (val / 10000).toFixed(0); },
      },
    },
    series: buildSeries('capital_series'),
  });
  window.addEventListener('resize', function() { chart2.resize(); });

  // Chart 3: Limit Up Count
  var chart3 = echarts.init(document.getElementById('chart-limit'), null, { renderer: 'svg' });
  chart3.setOption({
    color: palette,
    animation: false,
    tooltip: {
      trigger: 'axis',
      appendToBody: true,
      backgroundColor: bg2,
      borderColor: rule,
      textStyle: { color: ink },
      valueFormatter: function(val) { return val + ' 只'; },
    },
    legend: {
      data: sectors.map(function(s) { return s.name; }),
      top: 0,
      textStyle: { color: muted, fontSize: 11 },
      type: 'scroll',
    },
    grid: { top: 50, left: 50, right: 30, bottom: 40 },
    xAxis: {
      type: 'category',
      data: dateLabels,
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: muted, fontSize: 10, rotate: 30 },
    },
    yAxis: {
      type: 'value',
      name: '涨停数',
      nameTextStyle: { color: muted },
      axisLine: { lineStyle: { color: rule } },
      splitLine: { lineStyle: { color: rule, type: 'dashed', opacity: 0.3 } },
      axisLabel: { color: muted },
    },
    series: buildSeries('limit_series'),
  });
  window.addEventListener('resize', function() { chart3.resize(); });

  // Render lifecycle cards
  var gridEl = document.getElementById('lifecycle-grid');
  var tbodyEl = document.getElementById('lifecycle-tbody');

  var badgeClass = {
    '高潮': 'badge-climax',
    '崛起': 'badge-rising',
    '退烧': 'badge-cooling',
    '冷却': 'badge-cooling',
  };

  // Sort by current heat descending
  var sorted = sectors.slice().sort(function(a, b) {
    return b.current_heat - a.current_heat;
  });

  sorted.forEach(function(sec) {
    var lc = sec.lifecycle || {};
    var state = lc.state || '未知';
    var heat = sec.current_heat;
    var heatColor = heat >= 0 ? red : green; // 正热度=红(热), 负热度=绿(冷)

    // Card
    var card = document.createElement('div');
    card.className = 'lifecycle-card';
    card.innerHTML =
      '<span class="name">' + sec.name + '</span>' +
      '<span class="heat" style="color:' + heatColor + '">' +
        (heat >= 0 ? '+' : '') + heat.toFixed(1) +
      '</span>' +
      '<span class="badge ' + (badgeClass[state] || 'badge-cooling') + '">' + state + '</span>';
    gridEl.appendChild(card);

    // Table row
    var tr = document.createElement('tr');
    var heatClass = heat >= 0 ? 'positive' : 'negative'; // positive=red(hot), negative=green(cold)
    tr.innerHTML =
      '<td>' + sec.name + '</td>' +
      '<td class="' + heatClass + '">' + (heat >= 0 ? '+' : '') + heat.toFixed(1) + '</td>' +
      '<td>' + state + '</td>' +
      '<td>' + (lc.trend || '-') + '</td>' +
      '<td style="text-align:left;white:normal">' + (lc.description || '-') + '</td>';
    tbodyEl.appendChild(tr);
  });

})();
