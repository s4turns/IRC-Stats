/* ── IRC Stats — app.js ────────────────────────────────────────────────── */
'use strict';

/* ── IRC formatting → HTML ───────────────────────────────────────────────── */
const IRC_COLORS = [
  '#ffffff','#000000','#00007f','#009300','#ff0000','#7f0000',
  '#9c009c','#fc7f00','#ffff00','#00fc00','#009393','#00ffff',
  '#0000fc','#ff00ff','#7f7f7f','#d2d2d2',
];
function ircToHtml(raw) {
  let out = '', bold = false, italic = false, underline = false;
  let fg = null, bg = null;
  const flush = (txt) => {
    if (!txt) return;
    let style = '';
    if (fg !== null) style += `color:${IRC_COLORS[fg] ?? ''};`;
    if (bg !== null) style += `background:${IRC_COLORS[bg] ?? ''};`;
    const tags = [];
    if (bold)      tags.push('<b>');
    if (italic)    tags.push('<i>');
    if (underline) tags.push('<u>');
    const close = [...tags].reverse().map(t => '</' + t.slice(1)).join('');
    const open  = tags.join('');
    const wrap  = style ? `<span style="${style}">` : '';
    const wrapC = style ? '</span>' : '';
    out += wrap + open + escHtml(txt) + (close||'') + wrapC;
  };
  let i = 0, buf = '';
  while (i < raw.length) {
    const ch = raw[i];
    if (ch === '\x02') { flush(buf); buf=''; bold=!bold; i++; }
    else if (ch === '\x1d') { flush(buf); buf=''; italic=!italic; i++; }
    else if (ch === '\x1f') { flush(buf); buf=''; underline=!underline; i++; }
    else if (ch === '\x16') { flush(buf); buf=''; [fg,bg]=[bg,fg]; i++; }
    else if (ch === '\x0f') { flush(buf); buf=''; bold=italic=underline=false; fg=bg=null; i++; }
    else if (ch === '\x03') {
      flush(buf); buf=''; i++;
      const m = raw.slice(i).match(/^(\d{1,2})(?:,(\d{1,2}))?/);
      if (m) { fg=parseInt(m[1]); bg=m[2]!=null?parseInt(m[2]):null; i+=m[0].length; }
      else { fg=bg=null; }
    } else { buf += ch; i++; }
  }
  flush(buf);
  return out;
}

// DATA is injected by the template as: const DATA = { ... };

let currentPeriod = 'all';
let hourlyChart = null;
let weekdayChart = null;
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const HOURS = Array.from({length: 24}, (_, i) => `${String(i).padStart(2,'0')}:00`);
const WC_COLORS = ['#58a6ff','#3fb950','#bc8cff','#d29922','#f85149','#39d353','#388bfd','#a371f7'];

/* ── Init ────────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  if (typeof DATA === 'undefined') { console.error('DATA not loaded'); return; }
  initPeriodButtons();
  initHeatmap(DATA.stats.all.daily_activity);
  initCharts(DATA.stats.all);
  initTable(DATA.stats.all.users);
  initNonTopTable();
  initAwards(DATA.stats.all.awards);
  initBigNumbers(DATA.stats.all);
  initWordCloud(DATA.stats.all.word_cloud);
  initNetwork(DATA.stats.all.mention_network);
  initTopics(DATA.stats.all.topics);
  initSearch();
  updateStatCards(DATA.stats.all);
});

/* ── Period switching ────────────────────────────────────────────────────── */
function initPeriodButtons() {
  document.querySelectorAll('.period-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const period = btn.dataset.period;
      if (period === currentPeriod) return;
      currentPeriod = period;
      document.querySelectorAll('.period-btn').forEach(b => b.classList.toggle('active', b.dataset.period === period));
      const stats = DATA.stats[period];
      updateStatCards(stats);
      updateCharts(stats);
      initTable(stats.users);
      initAwards(stats.awards);
      initBigNumbers(stats);
    });
  });
}

/* ── Stat cards ──────────────────────────────────────────────────────────── */
function updateStatCards(stats) {
  setText('stat-total-lines',  fmt(stats.total_lines));
  setText('stat-unique-users', fmt(stats.unique_nicks));
  setText('stat-active-days',  fmt(stats.active_days) + ' active days');
  setText('stat-mad',  stats.most_active_day?.date || '—');
  setText('stat-mad-sub', stats.most_active_day?.lines ? fmt(stats.most_active_day.lines) + ' msgs' : '');
  const peakHour = stats.hourly ? stats.hourly.indexOf(Math.max(...stats.hourly)) : 0;
  setText('stat-peak-hour', `${String(peakHour).padStart(2,'0')}:00`);
  setText('stat-total-words', fmt(stats.total_words) + ' words');
}

/* ── Heatmap — SVG-based, labels and cells in one element ───────────────── */
const HEAT_COLORS = ['#161b22', '#0a3a1e', '#0d5235', '#1a7a45', '#26a641'];
const HEAT_STROKE = '#30363d';   // subtle cell border — makes grid visible without brightness
const CELL  = 13;   // cell size px
const CGAP  = 2;    // gap between cells
const CSTEP = CELL + CGAP;   // 15px per cell slot
const LABEL_H = 16; // height reserved for month labels above the grid

function buildHeatmap(container, daily) {
  if (!container) return;
  daily = daily || {};

  if (!Object.keys(daily).length) {
    container.innerHTML = '<p style="color:var(--text-muted);padding:16px 0;font-size:12px">No activity data for this period.</p>';
    return;
  }

  const NS = 'http://www.w3.org/2000/svg';

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const startDay = new Date(today);
  startDay.setDate(startDay.getDate() - 363);
  while (startDay.getDay() !== 1) startDay.setDate(startDay.getDate() - 1); // rewind to Monday

  const maxVal = Object.keys(daily).length ? Math.max(1, ...Object.values(daily)) : 1;

  // First pass: count total weeks so we know SVG width
  let totalWeeks = 0;
  { const d = new Date(startDay); while (d <= today) { if (d.getDay() === 1) totalWeeks++; d.setDate(d.getDate() + 1); } }

  const svgW = totalWeeks * CSTEP + CELL;       // exact pixel width
  const svgH = LABEL_H + 7 * CSTEP;             // labels + 7 rows

  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('width',  svgW + 'px');
  svg.setAttribute('height', svgH + 'px');
  svg.setAttribute('aria-label', 'Activity heatmap');


  let col = -1;
  let lastMonth = -1;
  const day = new Date(startDay);

  while (day <= today) {
    const dow = day.getDay();           // 0=Sun … 6=Sat
    const row = dow === 0 ? 6 : dow - 1; // Mon=0 … Sun=6

    if (dow === 1) {   // Monday → new column
      col++;
      // Only label when the 1st of a month falls within this week [day .. day+6].
      // This prevents "FebMar" crushing when the heatmap starts late in a month.
      const wk = new Date(day);
      let labelMonth = -1, labelYear = day.getFullYear();
      for (let i = 0; i < 7; i++) {
        if (wk.getDate() === 1) { labelMonth = wk.getMonth(); labelYear = wk.getFullYear(); break; }
        wk.setDate(wk.getDate() + 1);
      }
      if (labelMonth !== -1 && labelMonth !== lastMonth) {
        const t = document.createElementNS(NS, 'text');
        t.setAttribute('x', col * CSTEP);
        t.setAttribute('y', LABEL_H - 3);
        t.setAttribute('fill', '#8b949e');
        t.setAttribute('font-size', '10');
        t.setAttribute('font-family', 'system-ui,sans-serif');
        t.textContent = new Date(labelYear, labelMonth, 1).toLocaleString('default', { month: 'short' });
        svg.appendChild(t);
        lastMonth = labelMonth;
      }
    }

    const ds    = `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`;
    const count = daily[ds] || 0;
    const level = count === 0 ? 0 : count < maxVal * 0.25 ? 1 : count < maxVal * 0.5 ? 2 : count < maxVal * 0.75 ? 3 : 4;

    const rect = document.createElementNS(NS, 'rect');
    rect.setAttribute('x',            col * CSTEP);
    rect.setAttribute('y',            LABEL_H + row * CSTEP);
    rect.setAttribute('width',        CELL);
    rect.setAttribute('height',       CELL);
    rect.setAttribute('rx',           '2');
    rect.setAttribute('fill',         HEAT_COLORS[level]);
    rect.setAttribute('stroke',       HEAT_STROKE);
    rect.setAttribute('stroke-width', '0.5');

    const title = document.createElementNS(NS, 'title');
    title.textContent = `${ds}: ${count.toLocaleString()} msgs`;
    rect.appendChild(title);

    svg.appendChild(rect);
    day.setDate(day.getDate() + 1);
  }

  container.innerHTML = '';
  container.appendChild(svg);
}

function initHeatmap(daily) {
  buildHeatmap(document.getElementById('heatmap-container'), daily);
}

/* ── Charts ──────────────────────────────────────────────────────────────── */
const CHART_OPTS_BASE = {
  responsive: true, maintainAspectRatio: false,
  plugins: { legend: { display: false }, tooltip: {
    backgroundColor: '#1c2128', borderColor: '#30363d', borderWidth: 1,
    titleColor: '#e6edf3', bodyColor: '#8b949e',
  }},
  scales: {
    x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e', font: { size: 11 } } },
    y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e', font: { size: 11 } }, beginAtZero: true },
  },
};

function initCharts(stats) {
  const hCtx = document.getElementById('chart-hourly');
  const wCtx = document.getElementById('chart-weekday');
  if (!hCtx || !wCtx) return;

  hourlyChart = new Chart(hCtx, {
    type: 'bar',
    data: {
      labels: HOURS,
      datasets: [{ data: stats.hourly, backgroundColor: '#1f6feb', hoverBackgroundColor: '#58a6ff', borderRadius: 3 }]
    },
    options: {...CHART_OPTS_BASE},
  });

  weekdayChart = new Chart(wCtx, {
    type: 'bar',
    data: {
      labels: DAYS,
      datasets: [{ data: stats.weekday, backgroundColor: '#238636', hoverBackgroundColor: '#3fb950', borderRadius: 3 }]
    },
    options: {...CHART_OPTS_BASE},
  });
}

function updateCharts(stats) {
  if (hourlyChart) {
    hourlyChart.data.datasets[0].data = stats.hourly;
    hourlyChart.update();
  }
  if (weekdayChart) {
    weekdayChart.data.datasets[0].data = stats.weekday;
    weekdayChart.update();
  }
}

/* ── Top Chatters Table ──────────────────────────────────────────────────── */
let tableUsers = [];
let sortCol = 'lines';
let sortAsc = false;

function initTable(users) {
  tableUsers = users || [];
  renderTable();
  // Hook up sort headers once
  if (!document.querySelector('.stats-table th[data-sort]._hooked')) {
    document.querySelectorAll('.stats-table th[data-sort]').forEach(th => {
      th.classList.add('_hooked');
      th.addEventListener('click', () => {
        const col = th.dataset.sort;
        if (sortCol === col) sortAsc = !sortAsc;
        else { sortCol = col; sortAsc = false; }
        document.querySelectorAll('.stats-table th[data-sort]').forEach(t => {
          t.classList.remove('sorted');
          t.querySelector('.sort-arrow').textContent = '↕';
        });
        th.classList.add('sorted');
        th.querySelector('.sort-arrow').textContent = sortAsc ? '↑' : '↓';
        renderTable();
      });
    });
  }
}

function renderTable() {
  const tbody = document.getElementById('table-body');
  if (!tbody) return;
  const filter = (document.getElementById('user-search')?.value || '').toLowerCase();

  let rows = tableUsers.filter(u => !filter || u.nick.toLowerCase().includes(filter));
  rows.sort((a, b) => {
    const av = a[sortCol] ?? 0, bv = b[sortCol] ?? 0;
    return sortAsc ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
  });

  const maxLines = rows[0]?.lines || 1;
  const topByLines = [...tableUsers].sort((a,b) => b.lines - a.lines);

  tbody.innerHTML = '';
  rows.forEach((u, i) => {
    const rank = topByLines.findIndex(x => x.nick === u.nick) + 1;
    const rankClass = rank === 1 ? 'gold' : rank === 2 ? 'silver' : rank === 3 ? 'bronze' : '';
    const barPct = Math.round(u.lines / maxLines * 100);
    const tr = document.createElement('tr');
    tr.dataset.nick = u.nick.toLowerCase();
    tr.innerHTML = `
      <td><span class="rank-badge ${rankClass}">${rank}</span></td>
      <td><a class="nick-link" href="users/#${u.safe_nick}">${escHtml(u.nick)}</a></td>
      <td><strong>${fmt(u.lines)}</strong></td>
      <td class="tag-muted">${u.pct}%</td>
      <td>
        <div class="activity-bar" data-tooltip="${fmt(u.lines)} lines">
          <div class="activity-bar-fill" style="width:${barPct}%"></div>
        </div>
      </td>
      <td class="tag-muted">${u.avg_wpl || '—'}</td>
      <td class="tag-muted">${u.active_days}</td>
      <td class="tag-muted">${u.last_seen || '—'}</td>
    `;
    tbody.appendChild(tr);
  });

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="no-data">No users found.</td></tr>';
  }
}

/* ── Did Not Make the List ───────────────────────────────────────────────── */
function initNonTopTable() {
  const users = DATA.non_top_users;
  const section = document.getElementById('non-top-section');
  if (!users?.length) { section?.remove(); return; }

  const topCount = DATA.stats.all.users.length;
  setText('non-top-count', `${users.length} users`);

  const tbody = document.getElementById('non-top-body');
  if (tbody) {
    users.forEach((u, i) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><span class="rank-badge">${topCount + i + 1}</span></td>
        <td style="color:var(--text-secondary)">${escHtml(u.nick)}</td>
        <td>${fmt(u.lines)}</td>
        <td class="tag-muted">${fmt(u.words)}</td>
        <td class="tag-muted">${u.avg_wpl || '—'}</td>
        <td class="tag-muted">${u.active_days}</td>
        <td class="tag-muted">${u.last_seen || '—'}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  const toggle  = document.getElementById('non-top-toggle');
  const wrapper = document.getElementById('non-top-wrapper');
  if (toggle && wrapper) {
    toggle.addEventListener('click', () => {
      const open = wrapper.style.display !== 'none';
      wrapper.style.display = open ? 'none' : '';
      toggle.textContent = open ? 'Show ▼' : 'Hide ▲';
    });
  }
}

/* ── Awards ──────────────────────────────────────────────────────────────── */
const AWARD_DEFS = [
  { key: 'most_questions',    emoji: '❓', label: 'Question Master',  fmt: v => `${v}% of lines are questions` },
  { key: 'most_caps',         emoji: '🔊', label: 'ALL CAPS USER',    fmt: v => `${v}% lines in CAPS` },
  { key: 'most_smileys',      emoji: '😄', label: 'Happy Chatter',    fmt: v => `${v}% lines with smileys` },
  { key: 'most_urls',         emoji: '🔗', label: 'Link Sharer',      fmt: v => `${fmt(v)} URLs shared` },
  { key: 'night_owl',         emoji: '🦉', label: 'Night Owl',        fmt: v => `${v}% of msgs after midnight` },
  { key: 'morning_bird',      emoji: '🐦', label: 'Early Bird',       fmt: v => `${v}% of msgs before noon` },
  { key: 'most_actions',      emoji: '🎭', label: 'Action King',      fmt: v => `${fmt(v)} /me actions` },
  { key: 'most_kicks_given',  emoji: '🥾', label: 'Kick Master',      fmt: v => `${fmt(v)} kicks given` },
  { key: 'most_kicks_received',emoji:'💥', label: 'Kick Magnet',      fmt: v => `${fmt(v)} times kicked` },
  { key: 'most_bans_given',   emoji: '🔨', label: 'Ban Hammer',       fmt: v => `${fmt(v)} bans set` },
  { key: 'most_consistent',   emoji: '📅', label: 'Most Consistent',  fmt: v => `Active ${fmt(v)} days` },
  { key: 'wordiest',          emoji: '📝', label: 'Wordiest',         fmt: v => `${v} words per line avg` },
  { key: 'quietest',          emoji: '🤫', label: 'Short & Sweet',    fmt: v => `${v} words per line avg` },
];

function initAwards(awards) {
  const grid = document.getElementById('awards-grid');
  if (!grid || !awards) return;
  grid.innerHTML = '';
  AWARD_DEFS.forEach(def => {
    const aw = awards[def.key];
    if (!aw) return;
    const safeNick = aw.nick.replace(/[^a-zA-Z0-9\-_]/g, '_');
    const card = document.createElement('div');
    card.className = 'award-card';
    card.innerHTML = `
      <div class="award-emoji">${def.emoji}</div>
      <div class="award-body">
        <div class="award-label">${def.label}</div>
        <div class="award-nick"><a href="users/#${safeNick}">${escHtml(aw.nick)}</a></div>
        <div class="award-detail">${def.fmt(aw.value)}</div>
      </div>
    `;
    grid.appendChild(card);
  });
  if (!grid.children.length) {
    grid.innerHTML = '<p class="no-data">No award data available.</p>';
  }
}

/* ── Big Numbers ─────────────────────────────────────────────────────────── */
function initBigNumbers(stats) {
  const grid = document.getElementById('bignumbers-grid');
  if (!grid || !stats?.users?.length) return;
  grid.innerHTML = '';

  const users = stats.users;
  const nick = (u) => {
    const sn = u.nick.replace(/[^a-zA-Z0-9\-_]/g, '_');
    return `<a href="users/#${sn}">${escHtml(u.nick)}</a>`;
  };
  // questions/caps/smileys are stored as percentages 0–100
  const fpct = (n) => n != null ? (+(n)).toFixed(1) + '%' : '0%';

  const cards = [];
  const add = (label, stat, blurb) => {
    if (stat !== null && stat !== undefined && stat !== '' && stat !== '—') {
      cards.push({ label, stat, blurb });
    }
  };

  // Most lines
  const top = users[0];
  if (top) add('Chatterbox', fmt(top.lines) + ' lines',
    `<strong>${nick(top)}</strong> couldn't stop talking. ${fmt(top.lines)} lines — that's ${fpct(top.pct)} of all messages.`);

  // Most words
  const wordiest = [...users].sort((a,b)=>(b.words||0)-(a.words||0))[0];
  if (wordiest) add('Most Words', fmt(wordiest.words) + ' words',
    `<strong>${nick(wordiest)}</strong> typed the most words — <strong>${fmt(wordiest.words)}</strong> total.`);

  // Highest avg words per line
  const wplUser = [...users].filter(u=>u.lines>=20).sort((a,b)=>(b.avg_wpl||0)-(a.avg_wpl||0))[0];
  if (wplUser) add('Wordiest per Line', (wplUser.avg_wpl||0).toFixed(1) + ' WPL',
    `<strong>${nick(wplUser)}</strong> averages <strong>${(wplUser.avg_wpl||0).toFixed(1)}</strong> words per message. Never a short answer.`);

  // Shortest avg words per line (min 20 lines)
  const tersest = [...users].filter(u=>u.lines>=20).sort((a,b)=>(a.avg_wpl||99)-(b.avg_wpl||99))[0];
  if (tersest && tersest !== wplUser) add('Strong Silent Type', (tersest.avg_wpl||0).toFixed(1) + ' WPL',
    `<strong>${nick(tersest)}</strong> keeps it short at <strong>${(tersest.avg_wpl||0).toFixed(1)}</strong> words per line. Efficiency at its finest.`);

  // Most questions (stored as % of lines, threshold >5%)
  const questioner = [...users].filter(u=>u.lines>=10).sort((a,b)=>(b.questions||0)-(a.questions||0))[0];
  if (questioner && (questioner.questions||0) > 5) add('Question Machine', fpct(questioner.questions) + ' questions',
    `<strong>${nick(questioner)}</strong> ended <strong>${fpct(questioner.questions)}</strong> of their messages with a "?". So many questions.`);

  // Most CAPS (stored as % of lines, threshold >5%)
  const shouter = [...users].filter(u=>u.lines>=10).sort((a,b)=>(b.caps||0)-(a.caps||0))[0];
  if (shouter && (shouter.caps||0) > 5) add('Loud as Hell', fpct(shouter.caps) + ' CAPS',
    `<strong>${nick(shouter)}</strong> was typing in caps <strong>${fpct(shouter.caps)}</strong> of the time. CALM DOWN.`);

  // Most smileys (stored as % of lines)
  const smileyUser = [...users].filter(u=>u.lines>=10).sort((a,b)=>(b.smileys||0)-(a.smileys||0))[0];
  if (smileyUser && (smileyUser.smileys||0) > 0) add('Smiley Addict', fpct(smileyUser.smileys) + ' of lines',
    `<strong>${nick(smileyUser)}</strong> had a smiley in <strong>${fpct(smileyUser.smileys)}</strong> of their messages. :) :D :P`);

  // Most URLs
  const urlUser = [...users].sort((a,b)=>(b.urls||0)-(a.urls||0))[0];
  if (urlUser && (urlUser.urls||0) > 0) add('URL Spammer', fmt(urlUser.urls) + ' links',
    `<strong>${nick(urlUser)}</strong> shared <strong>${fmt(urlUser.urls)}</strong> URLs. Nobody asked, but thanks.`);

  // Most slaps given
  const slapper = [...users].sort((a,b)=>(b.slaps_given||0)-(a.slaps_given||0))[0];
  if (slapper && (slapper.slaps_given||0) > 0) add('Slap Happy', fmt(slapper.slaps_given) + ' slaps',
    `<strong>${nick(slapper)}</strong> slapped people <strong>${fmt(slapper.slaps_given)}</strong> times. Aggressive.`);

  // Most slapped
  const slapTarget = [...users].sort((a,b)=>(b.slaps_received||0)-(a.slaps_received||0))[0];
  if (slapTarget && (slapTarget.slaps_received||0) > 0) add('Human Punching Bag', fmt(slapTarget.slaps_received) + ' times slapped',
    `<strong>${nick(slapTarget)}</strong> got slapped <strong>${fmt(slapTarget.slaps_received)}</strong> times. What did you do?`);

  // Most active days
  const consistent = [...users].sort((a,b)=>(b.active_days||0)-(a.active_days||0))[0];
  if (consistent) add('Most Consistent', fmt(consistent.active_days) + ' days',
    `<strong>${nick(consistent)}</strong> showed up on <strong>${fmt(consistent.active_days)}</strong> different days. Rain or shine.`);

  // Most kicks given
  const kicker = [...users].sort((a,b)=>(b.kicks_given||0)-(a.kicks_given||0))[0];
  if (kicker && (kicker.kicks_given||0) > 0) add('Trigger Finger', fmt(kicker.kicks_given) + ' kicks',
    `<strong>${nick(kicker)}</strong> kicked people <strong>${fmt(kicker.kicks_given)}</strong> times. Power trip much?`);

  // Most kicked
  const kicked = [...users].sort((a,b)=>(b.kicks_received||0)-(a.kicks_received||0))[0];
  if (kicked && (kicked.kicks_received||0) > 0) add('Can\'t Stay Out', fmt(kicked.kicks_received) + ' times kicked',
    `<strong>${nick(kicked)}</strong> was kicked <strong>${fmt(kicked.kicks_received)}</strong> times and kept coming back.`);

  // Most actions
  const actioner = [...users].sort((a,b)=>(b.actions||0)-(a.actions||0))[0];
  if (actioner && (actioner.actions||0) > 0) add('Most Active', fmt(actioner.actions) + ' /me actions',
    `<strong>${nick(actioner)}</strong> used /me <strong>${fmt(actioner.actions)}</strong> times. Very expressive.`);

  cards.forEach(({ label, stat, blurb }) => {
    const div = document.createElement('div');
    div.className = 'bn-card';
    div.innerHTML = `
      <div class="bn-label">${escHtml(label)}</div>
      <div class="bn-stat">${stat}</div>
      <div class="bn-blurb">${blurb}</div>
    `;
    grid.appendChild(div);
  });

  if (!grid.children.length) {
    grid.innerHTML = '<p class="no-data">No data available.</p>';
  }
}

/* ── Word Cloud ──────────────────────────────────────────────────────────── */
function initWordCloud(words) {
  const container = document.getElementById('word-cloud');
  if (!container || !words?.length) return;
  container.innerHTML = '';
  const maxW = words[0][1] || 1;
  words.slice(0, 120).forEach(([word, count], i) => {
    const size = 11 + Math.round((count / maxW) * 28);
    const color = WC_COLORS[i % WC_COLORS.length];
    const span = document.createElement('span');
    span.className = 'wc-word';
    span.textContent = word;
    span.style.fontSize = `${size}px`;
    span.style.color = color;
    span.style.opacity = 0.7 + (count / maxW) * 0.3;
    span.dataset.tooltip = `"${word}" — ${fmt(count)} uses`;
    container.appendChild(span);
  });
}

/* ── Mention Network (D3) ────────────────────────────────────────────────── */
function _networkZoomToFit(svg, zoom, g, W, H) {
  try {
    const bb = g.node().getBBox();
    if (!bb.width || !bb.height) return;
    const pad = 60;
    const scale = Math.min(0.95, (W - pad * 2) / bb.width, (H - pad * 2) / bb.height);
    const tx = W / 2 - scale * (bb.x + bb.width  / 2);
    const ty = H / 2 - scale * (bb.y + bb.height / 2);
    svg.transition().duration(700)
      .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
  } catch(_) {}
}

function initNetwork(network) {
  const svgEl = document.getElementById('network-graph');
  if (!svgEl || typeof d3 === 'undefined') return;
  if (!network?.nodes?.length) {
    svgEl.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#484f58" dy=".3em">No mention data</text>';
    return;
  }

  const W = Math.max(svgEl.getBoundingClientRect().width, 700);
  const H = parseInt(getComputedStyle(svgEl).height) || 720;

  const zoom = d3.zoom().scaleExtent([0.05, 6]).on('zoom', e => g.attr('transform', e.transform));

  const svg = d3.select(svgEl)
    .attr('viewBox', `0 0 ${W} ${H}`)
    .call(zoom);

  svg.selectAll('*').remove();
  const g = svg.append('g');

  const maxLines  = d3.max(network.nodes, d => d.lines) || 1;
  const maxWeight = d3.max(network.links, d => d.value) || 1;
  const nodeRadius = d => 9 + (d.lines / maxLines) * 22;

  const simulation = d3.forceSimulation(network.nodes)
    .force('link',      d3.forceLink(network.links).id(d => d.id).distance(220).strength(0.12))
    .force('charge',    d3.forceManyBody().strength(-1100))
    .force('center',    d3.forceCenter(W / 2, H / 2).strength(0.03))
    .force('collision', d3.forceCollide(d => nodeRadius(d) + 32))
    .alphaDecay(0.02);

  // Edges
  const link = g.append('g').selectAll('line')
    .data(network.links).join('line')
    .attr('stroke', '#30363d')
    .attr('stroke-width', d => 1 + (d.value / maxWeight) * 5)
    .attr('stroke-opacity', 0.55);

  // Node groups
  const node = g.append('g').selectAll('g')
    .data(network.nodes).join('g')
    .call(d3.drag()
      .on('start', (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end',   (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; })
    );

  node.append('circle')
    .attr('r', nodeRadius)
    .attr('fill', '#1f6feb')
    .attr('stroke', '#58a6ff')
    .attr('stroke-width', 1.5);

  node.append('text')
    .text(d => d.id)
    .attr('text-anchor', 'middle')
    .attr('dy', d => nodeRadius(d) + 14)
    .attr('fill', '#c9d1d9')
    .attr('font-size', '11px')
    .attr('font-weight', '600')
    .attr('pointer-events', 'none');

  node.append('title').text(d => `${d.id}: ${fmt(d.lines)} lines`);

  node.on('click', (event, d) => {
    const safeNick = d.id.replace(/[^a-zA-Z0-9\-_]/g, '_');
    window.location.href = `users/#${safeNick}`;
  }).style('cursor', 'pointer');

  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  // Auto zoom-to-fit once simulation settles
  simulation.on('end', () => _networkZoomToFit(svg, zoom, g, W, H));

  // Controls
  document.getElementById('graph-reset')?.addEventListener('click', () => {
    _networkZoomToFit(svg, zoom, g, W, H);
  });
  document.getElementById('graph-restart')?.addEventListener('click', () => {
    simulation.alpha(0.8).restart();
  });
}

/* ── Topics ──────────────────────────────────────────────────────────────── */
function initTopics(topics) {
  const list = document.getElementById('topics-list');
  if (!list) return;
  if (!topics?.length) { list.innerHTML = '<p class="no-data">No topic data available.</p>'; return; }
  list.innerHTML = '';
  topics.slice(0, 20).forEach(t => {
    const item = document.createElement('div');
    item.className = 'topic-item';
    const by = t.nick ? `<span class="topic-nick">${escHtml(t.nick)}</span>` : '';
    const sep = t.nick && t.timestamp ? ' · ' : '';
    item.innerHTML = `
      <div class="topic-text">${ircToHtml(t.text)}</div>
      <div class="topic-meta">${by}${sep}${escHtml(t.timestamp || '')}</div>
    `;
    list.appendChild(item);
  });
}

/* ── Search ──────────────────────────────────────────────────────────────── */
function initSearch() {
  const inp = document.getElementById('user-search');
  if (!inp) return;
  inp.addEventListener('input', () => renderTable());
}

/* ── User page — fetch-based single page ─────────────────────────────────── */
let _userHourlyChart = null;
let _userWeekdayChart = null;

async function initUserPageFromURL() {
  const safeNick = location.hash.slice(1);
  if (!safeNick) {
    document.getElementById('user-main').innerHTML =
      '<p class="no-data" style="padding:60px 0">No user specified. <a href="../index.html">Back to stats</a></p>';
    return;
  }

  // Show spinner while fetching
  document.getElementById('user-main').innerHTML =
    '<div style="text-align:center;padding:60px 0"><div class="spinner"></div></div>';

  try {
    const resp = await fetch(`data/${safeNick}.json`);
    if (!resp.ok) throw new Error(`User not found (${resp.status})`);
    const user = await resp.json();
    renderUserPage(user, safeNick);
  } catch (e) {
    document.getElementById('user-main').innerHTML =
      `<p class="no-data" style="padding:40px 0">Could not load user: ${escHtml(e.message)}</p>`;
  }
}

function renderUserPage(user, safeNick) {
  const nick = user.nick || safeNick;

  // ── Header ───────────────────────────────────────────────────────────────
  document.title = `${nick} — ${(typeof SITE !== 'undefined' ? SITE.channel : '')} IRC Stats`;
  const hue = (nick.length * 47) % 360;
  document.getElementById('user-avatar').style.background = `hsl(${hue},60%,35%)`;
  document.getElementById('user-avatar').textContent = nick[0].toUpperCase();
  document.getElementById('user-nick').textContent = nick;
  document.getElementById('user-dates').textContent =
    `Active since ${user.first_seen || '?'}  ·  Last seen ${user.last_seen || '?'}` +
    (typeof SITE !== 'undefined' ? `  ·  ${SITE.network}` : '');

  // ── Badges ───────────────────────────────────────────────────────────────
  const badgesEl = document.getElementById('user-badges');
  const mah = user.most_active_hour ?? 12;
  const badgeDefs = [
    // Activity level
    [user.lines > 5000,                        'badge-blue',   '💬 Chatterbox'],
    [user.lines > 1000 && user.lines <= 5000,  'badge-blue',   '🗣️ Regular'],
    // Time of day
    [mah >= 1  && mah <= 4,                    'badge-purple', '💀 Insomniac'],
    [mah >= 0  && mah <= 5 && !(mah >= 1 && mah <= 4), 'badge-purple', '🦉 Night Owl'],
    [mah >= 6  && mah <= 11,                   'badge-green',  '🌅 Early Bird'],
    // Message style
    [user.questions > 20,                      'badge-blue',   '❓ Question Addict'],
    [user.caps > 10,                           'badge-orange', '🔊 ALL CAPS'],
    [user.avg_wpl > 12,                        'badge-green',  '📝 Essayist'],
    [(user.avg_wpl < 3) && user.lines > 50,    'badge-blue',   '⚡ Sniper'],
    // Content
    [user.urls > 20,                           'badge-blue',   '🔗 Link Sharer'],
    [user.smileys > 15,                        'badge-green',  '😄 Happy Chatter'],
    [user.actions > 50,                        'badge-purple', '🎭 Action Hero'],
    // Social
    [user.kicks_given > 5,                     'badge-red',    '🥾 Kick Master'],
    [user.kicks_received > 3,                  'badge-orange', '💥 Kick Magnet'],
    [user.bans_given > 5,                      'badge-red',    '🔨 Ban Hammer'],
    [(user.mentions_given   && Object.keys(user.mentions_given).length >= 8),   'badge-blue',  '📢 Social Butterfly'],
    [(user.mentions_received && Object.keys(user.mentions_received).length >= 8),'badge-green', '⭐ Popular'],
    // Tenure & identity
    [user.active_days > 300,                   'badge-orange', '🏆 Veteran'],
    [(user.nicks_used?.length ?? 0) >= 4,      'badge-purple', '🔄 Shape-shifter'],
  ];
  badgesEl.innerHTML = badgeDefs
    .filter(([cond]) => cond)
    .map(([, cls, label]) => `<span class="badge ${cls}">${label}</span>`)
    .join('');

  // ── Main content ─────────────────────────────────────────────────────────
  document.getElementById('user-main').innerHTML = `
    <!-- Stat row 1 -->
    <div class="section stat-cards">
      ${statCard('Total Lines',       fmt(user.lines),       fmt(user.words) + ' words total')}
      ${statCard('Avg Words / Line',  user.avg_wpl ?? '—',   user.avg_cpl + ' chars / line')}
      ${statCard('Active Days',       fmt(user.active_days), fmt(user.joins) + ' joins')}
      ${statCard('Peak Hour',         pad2(user.most_active_hour) + ':00', fmt(user.actions) + ' /me actions')}
    </div>
    <!-- Stat row 2 -->
    <div class="section stat-cards">
      ${statCard('Questions Asked',   (user.questions ?? 0) + '%', 'of all lines')}
      ${statCard('ALL CAPS Lines',    (user.caps ?? 0) + '%',      'of all lines')}
      ${statCard('URLs Shared',       fmt(user.urls),              'links posted')}
      ${statCard('Operator Activity', (user.kicks_given||0) + ' kicks · ' + (user.bans_given||0) + ' bans', (user.kicks_received||0) + ' times kicked')}
    </div>
    <!-- Heatmap -->
    <div class="section card">
      <div class="section-header">
        <span class="section-icon">📅</span><h2>Personal Activity Heatmap</h2>
        <span class="section-count">last 52 weeks</span>
      </div>
      <div class="heatmap-scroll" id="user-heatmap-container"></div>
      <div class="heatmap-legend">
        Less
        <div class="heatmap-cell" data-level="0"></div><div class="heatmap-cell" data-level="1"></div>
        <div class="heatmap-cell" data-level="2"></div><div class="heatmap-cell" data-level="3"></div>
        <div class="heatmap-cell" data-level="4"></div>
        More
      </div>
    </div>
    <!-- Charts -->
    <div class="section charts-row">
      <div class="card">
        <p style="font-size:13px;font-weight:600;color:var(--text-secondary);margin-bottom:8px">Messages by Hour</p>
        <div class="chart-container"><canvas id="user-chart-hourly"></canvas></div>
      </div>
      <div class="card">
        <p style="font-size:13px;font-weight:600;color:var(--text-secondary);margin-bottom:8px">Messages by Day of Week</p>
        <div class="chart-container"><canvas id="user-chart-weekday"></canvas></div>
      </div>
    </div>
    ${user.quotes?.length ? `
    <!-- Quotes -->
    <div class="section card">
      <div class="section-header"><span class="section-icon">💬</span><h2>Random Quotes</h2></div>
      <div class="quotes-grid">${user.quotes.map(q => `<div class="quote-card"><span class="quote-nick">&lt;${escHtml(user.nick)}&gt;</span>${ircToHtml(q)}</div>`).join('')}</div>
    </div>` : ''}
    ${user.top_words?.length ? `
    <!-- Word cloud -->
    <div class="section card">
      <div class="section-header"><span class="section-icon">🔤</span><h2>Favourite Words</h2></div>
      <div class="word-cloud" id="user-word-cloud"></div>
    </div>` : ''}
    <!-- Interactions -->
    <div class="section card">
      <div class="section-header"><span class="section-icon">🔗</span><h2>Interactions</h2></div>
      <div class="interactions-grid">
        <div>
          <p style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:10px;text-transform:uppercase;letter-spacing:.5px">
            Most mentioned by ${escHtml(nick)}</p>
          <ul class="interaction-list" id="mentions-given-list"></ul>
        </div>
        <div>
          <p style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:10px;text-transform:uppercase;letter-spacing:.5px">
            Who mentions ${escHtml(nick)} most</p>
          <ul class="interaction-list" id="mentions-received-list"></ul>
        </div>
      </div>
    </div>
    ${user.longest_line ? `
    <!-- Longest line -->
    <div class="section card">
      <div class="section-header"><span class="section-icon">📏</span><h2>Longest Message</h2></div>
      <div class="quote-card" style="border-left-color:var(--accent-purple)">${ircToHtml(user.longest_line)}</div>
    </div>` : ''}
    ${user.nicks_used?.length > 1 ? `
    <!-- Aliases -->
    <div class="section card">
      <div class="section-header"><span class="section-icon">🪪</span><h2>Known Aliases</h2></div>
      <div style="display:flex;flex-wrap:wrap;gap:8px">
        ${user.nicks_used.map(n => `<span class="badge badge-blue mono">${escHtml(n)}</span>`).join('')}
      </div>
    </div>` : ''}
  `;

  // ── Post-render init ──────────────────────────────────────────────────────
  buildHeatmap(document.getElementById('user-heatmap-container'), user.daily_lines || {});

  // Destroy previous chart instances before creating new ones
  if (_userHourlyChart)  { _userHourlyChart.destroy();  _userHourlyChart  = null; }
  if (_userWeekdayChart) { _userWeekdayChart.destroy(); _userWeekdayChart = null; }
  const hCtx = document.getElementById('user-chart-hourly');
  const wCtx = document.getElementById('user-chart-weekday');
  if (hCtx && user.hourly) {
    _userHourlyChart = new Chart(hCtx, {
      type: 'bar',
      data: { labels: HOURS, datasets: [{ data: user.hourly, backgroundColor: '#1f6feb', hoverBackgroundColor: '#58a6ff', borderRadius: 3 }] },
      options: {...CHART_OPTS_BASE},
    });
  }
  if (wCtx && user.weekday) {
    _userWeekdayChart = new Chart(wCtx, {
      type: 'bar',
      data: { labels: DAYS, datasets: [{ data: user.weekday, backgroundColor: '#238636', hoverBackgroundColor: '#3fb950', borderRadius: 3 }] },
      options: {...CHART_OPTS_BASE},
    });
  }

  // Word cloud
  if (user.top_words?.length) {
    const wc = document.getElementById('user-word-cloud');
    if (wc) {
      const maxW = user.top_words[0][1] || 1;
      user.top_words.slice(0, 60).forEach(([word, count], i) => {
        const span = document.createElement('span');
        span.className = 'wc-word';
        span.textContent = word;
        span.style.fontSize = `${11 + Math.round((count / maxW) * 24)}px`;
        span.style.color = WC_COLORS[i % WC_COLORS.length];
        span.style.opacity = 0.7 + (count / maxW) * 0.3;
        span.title = `"${word}" — ${fmt(count)} uses`;
        wc.appendChild(span);
      });
    }
  }

  // Interactions
  const renderMentions = (elId, obj) => {
    const el = document.getElementById(elId);
    if (!el) return;
    const topNicks = typeof DATA !== 'undefined'
      ? new Set((DATA.stats.all.users || []).map(u => u.nick.toLowerCase()))
      : null;
    const entries = Object.entries(obj || {})
      .filter(([n]) => n.length > 1 && (!topNicks || topNicks.has(n.toLowerCase())))
      .sort((a,b) => b[1]-a[1]).slice(0, 8);
    if (!entries.length) { el.innerHTML = '<li class="tag-muted">None recorded</li>'; return; }
    el.innerHTML = '';
    entries.forEach(([n, count]) => {
      const sn = n.replace(/[^a-zA-Z0-9\-_]/g, '_');
      const li = document.createElement('li');
      li.innerHTML = `<a href="#${sn}">${escHtml(n)}</a><span class="interaction-count">${count}×</span>`;
      el.appendChild(li);
    });
  };
  renderMentions('mentions-given-list',    user.mentions_given);
  renderMentions('mentions-received-list', user.mentions_received);
}

/* helpers for renderUserPage */
function statCard(label, value, sub) {
  return `<div class="stat-card">
    <div class="stat-card-label">${label}</div>
    <div class="stat-card-value" style="font-size:${String(value).length > 7 ? '20px' : '28px'}">${value}</div>
    <div class="stat-card-sub">${sub}</div>
  </div>`;
}
function pad2(n) { return String(n ?? 0).padStart(2, '0'); }

/* ── Helpers ─────────────────────────────────────────────────────────────── */
function fmt(n) {
  if (n === null || n === undefined) return '0';
  return Number(n).toLocaleString();
}
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}
/* ── Back to top ─────────────────────────────────────────────────────────── */
(function() {
  const btn = document.getElementById('back-to-top');
  if (!btn) return;
  window.addEventListener('scroll', () => {
    btn.classList.toggle('visible', window.scrollY > 400);
  }, { passive: true });
  btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
})();

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

