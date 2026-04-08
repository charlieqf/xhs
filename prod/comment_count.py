"""统计 comment_response*.json 的笔记回复数据，按日输出汇总与洞察，并生成美观的 HTML 报告。"""

import sys
import io

# 强制标准输出使用 UTF-8，避免 Windows 控制台 GBK 编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import json
import glob
import os
from collections import Counter, defaultdict
from datetime import datetime


def load_all_responses(directory: str) -> list[dict]:
    """加载目录下所有 comment_response*.json 文件。"""
    records = []
    patterns = [
        os.path.join(directory, "comment_response*.json"),
    ]
    seen_files = set()
    for pattern in patterns:
        for path in glob.glob(pattern):
            # 跳过子目录中的文件
            if os.path.dirname(os.path.abspath(path)) != os.path.abspath(directory):
                continue
            if path in seen_files:
                continue
            seen_files.add(path)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    records.extend(data)
    return records


def parse_date(timestamp: str) -> str:
    """从时间戳中提取日期（YYYY-MM-DD）。"""
    try:
        return timestamp.split(" ")[0]
    except Exception:
        return "unknown"


def compute_daily_stats(records: list[dict]) -> dict:
    """按日计算统计指标。"""
    daily = defaultdict(lambda: {
        "total": 0,
        "status_counter": Counter(),
        "keyword_counter": Counter(),
        "note_counter": Counter(),
        "user_counter": Counter(),
        "note_ids": set(),
        "user_ids": set(),
    })

    for rec in records:
        date = parse_date(rec.get("timestamp", ""))
        d = daily[date]
        d["total"] += 1
        d["status_counter"][rec.get("send_status", "unknown")] += 1
        d["keyword_counter"][rec.get("keyword", "unknown")] += 1
        d["note_counter"][rec.get("note_title", "unknown")] += 1
        d["user_counter"][rec.get("target_user", "unknown")] += 1
        d["note_ids"].add(rec.get("note_id", ""))
        d["user_ids"].add(rec.get("target_user", ""))

    return daily


def escape_html(text: str) -> str:
    """转义 HTML 特殊字符。"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate_html_report(daily: dict, output_path: str):
    """生成美观的 HTML 统计报告。"""
    if not daily:
        return

    sorted_dates = sorted(daily.keys())
    total_all = sum(d["total"] for d in daily.values())
    total_notes = len(set().union(*(d["note_ids"] for d in daily.values())))
    total_users = len(set().union(*(d["user_ids"] for d in daily.values())))

    all_keywords = Counter()
    all_notes = Counter()
    all_status = Counter()
    for d in daily.values():
        all_keywords += d["keyword_counter"]
        all_notes += d["note_counter"]
        all_status += d["status_counter"]

    days_count = len(sorted_dates)
    avg_per_day = total_all / days_count if days_count else 0
    max_date = max(sorted_dates, key=lambda d: daily[d]["total"])
    min_date = min(sorted_dates, key=lambda d: daily[d]["total"])
    success = all_status.get("success", 0)
    success_rate = success / total_all * 100 if total_all else 0

    # 构建每日数据 JSON
    daily_data = []
    for date in sorted_dates:
        d = daily[date]
        daily_data.append({
            "date": date,
            "total": d["total"],
            "notes": len(d["note_ids"]),
            "users": len(d["user_ids"]),
            "keywords": [{"name": k, "count": v} for k, v in d["keyword_counter"].most_common(8)],
            "notes_top": [{"name": k, "count": v} for k, v in d["note_counter"].most_common(8)],
            "users_top": [{"name": k, "count": v} for k, v in d["user_counter"].most_common(8)],
            "status": [{"name": k, "count": v} for k, v in d["status_counter"].most_common()],
        })

    daily_json = json.dumps(daily_data, ensure_ascii=False)
    global_keywords_json = json.dumps(
        [{"name": k, "count": v} for k, v in all_keywords.most_common(10)],
        ensure_ascii=False,
    )
    global_notes_json = json.dumps(
        [{"name": k, "count": v} for k, v in all_notes.most_common(10)],
        ensure_ascii=False,
    )

    # 每日趋势数据
    trend_dates = json.dumps(sorted_dates, ensure_ascii=False)
    trend_values = json.dumps([daily[d]["total"] for d in sorted_dates], ensure_ascii=False)
    trend_notes = json.dumps([len(daily[d]["note_ids"]) for d in sorted_dates], ensure_ascii=False)
    trend_users = json.dumps([len(daily[d]["user_ids"]) for d in sorted_dates], ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>评论回复统计报告</title>
<style>
  :root {{
    --bg: #f5f7fa;
    --card: #fff;
    --primary: #4f6ef7;
    --primary-light: #e8edff;
    --success: #22c55e;
    --warning: #f59e0b;
    --danger: #ef4444;
    --text: #1e293b;
    --text-secondary: #64748b;
    --border: #e2e8f0;
    --shadow: 0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06);
    --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.08), 0 4px 6px -2px rgba(0,0,0,0.04);
    --radius: 12px;
  }}

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding: 24px;
  }}

  .container {{ max-width: 1200px; margin: 0 auto; }}

  /* Header */
  .header {{
    text-align: center;
    padding: 40px 20px 32px;
    margin-bottom: 32px;
  }}
  .header h1 {{
    font-size: 28px;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 8px;
  }}
  .header .subtitle {{
    color: var(--text-secondary);
    font-size: 14px;
  }}
  .header .date-range {{
    display: inline-block;
    background: var(--primary-light);
    color: var(--primary);
    padding: 4px 14px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 500;
    margin-top: 12px;
  }}

  /* Summary Cards */
  .summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }}
  .summary-card {{
    background: var(--card);
    border-radius: var(--radius);
    padding: 24px;
    box-shadow: var(--shadow);
    text-align: center;
    transition: transform 0.2s, box-shadow 0.2s;
  }}
  .summary-card:hover {{
    transform: translateY(-2px);
    box-shadow: var(--shadow-lg);
  }}
  .summary-card .icon {{
    font-size: 32px;
    margin-bottom: 8px;
  }}
  .summary-card .value {{
    font-size: 32px;
    font-weight: 700;
    color: var(--primary);
    line-height: 1.2;
  }}
  .summary-card .label {{
    font-size: 13px;
    color: var(--text-secondary);
    margin-top: 4px;
  }}
  .summary-card.success .value {{ color: var(--success); }}
  .summary-card.warning .value {{ color: var(--warning); }}

  /* Section */
  .section {{
    background: var(--card);
    border-radius: var(--radius);
    padding: 28px;
    margin-bottom: 24px;
    box-shadow: var(--shadow);
  }}
  .section-title {{
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 2px solid var(--border);
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .section-title .dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--primary);
  }}

  /* Trend Chart */
  .chart-container {{
    position: relative;
    height: 280px;
    margin: 20px 0;
  }}
  .chart-container canvas {{
    width: 100% !important;
    height: 100% !important;
  }}

  /* Daily Tabs */
  .tabs {{
    display: flex;
    gap: 8px;
    margin-bottom: 20px;
    flex-wrap: wrap;
  }}
  .tab-btn {{
    padding: 8px 20px;
    border: 1px solid var(--border);
    background: var(--card);
    border-radius: 8px;
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    color: var(--text-secondary);
    transition: all 0.2s;
  }}
  .tab-btn:hover {{
    border-color: var(--primary);
    color: var(--primary);
  }}
  .tab-btn.active {{
    background: var(--primary);
    border-color: var(--primary);
    color: #fff;
  }}

  .tab-content {{ display: none; }}
  .tab-content.active {{ display: block; }}

  /* Stats Grid */
  .stats-row {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-bottom: 20px;
  }}
  .stat-item {{
    background: var(--bg);
    border-radius: 8px;
    padding: 16px;
    text-align: center;
  }}
  .stat-item .stat-value {{
    font-size: 24px;
    font-weight: 700;
    color: var(--primary);
  }}
  .stat-item .stat-label {{
    font-size: 12px;
    color: var(--text-secondary);
    margin-top: 2px;
  }}

  /* Bar Chart */
  .bar-chart {{ margin: 16px 0; }}
  .bar-item {{
    display: flex;
    align-items: center;
    margin-bottom: 10px;
    gap: 12px;
  }}
  .bar-label {{
    min-width: 100px;
    font-size: 13px;
    color: var(--text);
    text-align: right;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .bar-track {{
    flex: 1;
    height: 28px;
    background: var(--bg);
    border-radius: 6px;
    overflow: hidden;
    position: relative;
  }}
  .bar-fill {{
    height: 100%;
    border-radius: 6px;
    transition: width 0.6s ease;
    display: flex;
    align-items: center;
    padding-left: 8px;
  }}
  .bar-fill.primary {{ background: linear-gradient(90deg, #4f6ef7, #6c8cff); }}
  .bar-fill.success {{ background: linear-gradient(90deg, #22c55e, #4ade80); }}
  .bar-fill.warning {{ background: linear-gradient(90deg, #f59e0b, #fbbf24); }}
  .bar-fill.danger {{ background: linear-gradient(90deg, #ef4444, #f87171); }}
  .bar-fill.info {{ background: linear-gradient(90deg, #06b6d4, #22d3ee); }}
  .bar-count {{
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    min-width: 50px;
  }}

  /* Table */
  .data-table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 12px;
  }}
  .data-table th {{
    text-align: left;
    padding: 10px 12px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 2px solid var(--border);
  }}
  .data-table td {{
    padding: 10px 12px;
    font-size: 13px;
    border-bottom: 1px solid var(--border);
  }}
  .data-table tr:last-child td {{ border-bottom: none; }}
  .data-table tr:hover td {{ background: var(--bg); }}
  .rank {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
  }}
  .rank-1 {{ background: #fef3c7; color: #d97706; }}
  .rank-2 {{ background: #f1f5f9; color: #64748b; }}
  .rank-3 {{ background: #fed7aa; color: #ea580c; }}
  .rank-other {{ background: var(--bg); color: var(--text-secondary); }}

  /* Insight Cards */
  .insight-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
  }}
  .insight-card {{
    background: var(--bg);
    border-radius: 10px;
    padding: 20px;
  }}
  .insight-card h4 {{
    font-size: 14px;
    font-weight: 600;
    margin-bottom: 12px;
    color: var(--text);
  }}

  /* Status Badge */
  .status-badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 500;
  }}
  .status-badge.success {{ background: #dcfce7; color: #16a34a; }}
  .status-badge.failed {{ background: #fee2e2; color: #dc2626; }}

  /* Footer */
  .footer {{
    text-align: center;
    padding: 24px;
    color: var(--text-secondary);
    font-size: 12px;
  }}

  @media (max-width: 768px) {{
    body {{ padding: 12px; }}
    .stats-row {{ grid-template-columns: 1fr; }}
    .summary-grid {{ grid-template-columns: repeat(2, 1fr); }}
    .bar-label {{ min-width: 70px; font-size: 12px; }}
  }}
</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <div class="header">
    <h1>评论回复统计报告</h1>
    <p class="subtitle">小红书自动评论回复数据分析</p>
    <span class="date-range">{sorted_dates[0]} ~ {sorted_dates[-1]}</span>
  </div>

  <!-- Summary Cards -->
  <div class="summary-grid">
    <div class="summary-card">
      <div class="icon">💬</div>
      <div class="value">{total_all}</div>
      <div class="label">总回复数</div>
    </div>
    <div class="summary-card">
      <div class="icon">📝</div>
      <div class="value">{total_notes}</div>
      <div class="label">涉及笔记</div>
    </div>
    <div class="summary-card">
      <div class="icon">👥</div>
      <div class="value">{total_users}</div>
      <div class="label">涉及用户</div>
    </div>
    <div class="summary-card success">
      <div class="icon">✅</div>
      <div class="value">{success_rate:.1f}%</div>
      <div class="label">发送成功率</div>
    </div>
    <div class="summary-card warning">
      <div class="icon">📊</div>
      <div class="value">{avg_per_day:.0f}</div>
      <div class="label">日均回复</div>
    </div>
  </div>

  <!-- Trend Chart -->
  <div class="section">
    <div class="section-title"><span class="dot"></span>每日趋势</div>
    <div class="chart-container">
      <canvas id="trendChart"></canvas>
    </div>
  </div>

  <!-- Daily Breakdown -->
  <div class="section">
    <div class="section-title"><span class="dot"></span>每日明细</div>
    <div class="tabs" id="dayTabs"></div>
    <div id="dayContents"></div>
  </div>

  <!-- Global Insights -->
  <div class="section">
    <div class="section-title"><span class="dot"></span>全局洞察</div>
    <div class="insight-grid">
      <div class="insight-card">
        <h4>🔥 全局关键词 TOP 10</h4>
        <div id="globalKeywords" class="bar-chart"></div>
      </div>
      <div class="insight-card">
        <h4>📝 全局笔记 TOP 10</h4>
        <div id="globalNotes" class="bar-chart"></div>
      </div>
    </div>
    <div style="margin-top: 20px; padding: 16px; background: var(--bg); border-radius: 10px;">
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; text-align: center;">
        <div>
          <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 4px;">最高回复日</div>
          <div style="font-size: 18px; font-weight: 700; color: var(--success);">{max_date}</div>
          <div style="font-size: 13px; color: var(--text-secondary);">{daily[max_date]['total']} 条</div>
        </div>
        <div>
          <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 4px;">最低回复日</div>
          <div style="font-size: 18px; font-weight: 700; color: var(--warning);">{min_date}</div>
          <div style="font-size: 13px; color: var(--text-secondary);">{daily[min_date]['total']} 条</div>
        </div>
        <div>
          <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 4px;">数据跨度</div>
          <div style="font-size: 18px; font-weight: 700; color: var(--primary);">{days_count} 天</div>
          <div style="font-size: 13px; color: var(--text-secondary);">{sorted_dates[0]} 起</div>
        </div>
      </div>
    </div>
  </div>

  <div class="footer">
    生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 数据来源：comment_response*.json
  </div>

</div>

<script>
// 数据
const dailyData = {daily_json};
const trendDates = {trend_dates};
const trendValues = {trend_values};
const trendNotes = {trend_notes};
const trendUsers = {trend_users};
const globalKeywords = {global_keywords_json};
const globalNotes = {global_notes_json};

// 绘制趋势图
function drawTrendChart() {{
  const canvas = document.getElementById('trendChart');
  const ctx = canvas.getContext('2d');
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * 2;
  canvas.height = rect.height * 2;
  ctx.scale(2, 2);
  const W = rect.width;
  const H = rect.height;

  const padding = {{ top: 30, right: 30, bottom: 50, left: 60 }};
  const chartW = W - padding.left - padding.right;
  const chartH = H - padding.top - padding.bottom;

  const maxVal = Math.max(...trendValues) * 1.1;
  const n = trendValues.length;

  // Grid lines
  ctx.strokeStyle = '#e2e8f0';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {{
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(W - padding.right, y);
    ctx.stroke();

    ctx.fillStyle = '#64748b';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(Math.round(maxVal - (maxVal / 4) * i), padding.left - 8, y + 4);
  }}

  // Bars
  const barWidth = Math.min(chartW / n * 0.5, 60);
  const gap = chartW / n;

  trendValues.forEach((val, i) => {{
    const x = padding.left + gap * i + gap / 2;
    const barH = (val / maxVal) * chartH;
    const y = padding.top + chartH - barH;

    // Gradient
    const grad = ctx.createLinearGradient(0, y, 0, padding.top + chartH);
    grad.addColorStop(0, '#4f6ef7');
    grad.addColorStop(1, '#8ba3ff');
    ctx.fillStyle = grad;

    // Rounded rect
    const r = 4;
    ctx.beginPath();
    ctx.moveTo(x - barWidth/2 + r, y);
    ctx.lineTo(x + barWidth/2 - r, y);
    ctx.quadraticCurveTo(x + barWidth/2, y, x + barWidth/2, y + r);
    ctx.lineTo(x + barWidth/2, padding.top + chartH);
    ctx.lineTo(x - barWidth/2, padding.top + chartH);
    ctx.lineTo(x - barWidth/2, y + r);
    ctx.quadraticCurveTo(x - barWidth/2, y, x - barWidth/2 + r, y);
    ctx.fill();

    // Value label
    ctx.fillStyle = '#1e293b';
    ctx.font = 'bold 11px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(val, x, y - 6);

    // Date label
    ctx.fillStyle = '#64748b';
    ctx.font = '11px sans-serif';
    const dateLabel = trendDates[i].substring(5);
    ctx.fillText(dateLabel, x, H - padding.bottom + 20);
  }});

  // Y axis label
  ctx.save();
  ctx.translate(15, padding.top + chartH / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillStyle = '#64748b';
  ctx.font = '12px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('回复数', 0, 0);
  ctx.restore();
}}

// 渲染每日 Tab
function renderTabs() {{
  const tabsEl = document.getElementById('dayTabs');
  const contentsEl = document.getElementById('dayContents');

  dailyData.forEach((day, idx) => {{
    // Tab button
    const btn = document.createElement('button');
    btn.className = 'tab-btn' + (idx === 0 ? ' active' : '');
    btn.textContent = day.date.substring(5) + ' (' + day.total + ')';
    btn.onclick = () => switchTab(idx);
    tabsEl.appendChild(btn);

    // Tab content
    const div = document.createElement('div');
    div.className = 'tab-content' + (idx === 0 ? ' active' : '');
    div.id = 'day-' + idx;

    const maxKw = day.keywords.length > 0 ? day.keywords[0].count : 1;
    const maxNote = day.notes_top.length > 0 ? day.notes_top[0].count : 1;

    let html = `
      <div class="stats-row">
        <div class="stat-item">
          <div class="stat-value">${{day.total}}</div>
          <div class="stat-label">回复总数</div>
        </div>
        <div class="stat-item">
          <div class="stat-value">${{day.notes}}</div>
          <div class="stat-label">涉及笔记</div>
        </div>
        <div class="stat-item">
          <div class="stat-value">${{day.users}}</div>
          <div class="stat-label">涉及用户</div>
        </div>
      </div>
    `;

    // Status
    html += '<h4 style="font-size:14px;font-weight:600;margin:16px 0 10px;">发送状态</h4>';
    html += '<div class="bar-chart">';
    day.status.forEach(s => {{
      const pct = (s.count / day.total * 100).toFixed(1);
      const barClass = s.name === 'success' ? 'success' : 'danger';
      html += `
        <div class="bar-item">
          <span class="bar-label">${{s.name}}</span>
          <div class="bar-track">
            <div class="bar-fill ${{barClass}}" style="width:${{pct}}%"></div>
          </div>
          <span class="bar-count">${{s.count}} (${{pct}}%)</span>
        </div>
      `;
    }});
    html += '</div>';

    // Keywords
    html += '<h4 style="font-size:14px;font-weight:600;margin:16px 0 10px;">关键词分布</h4>';
    html += '<div class="bar-chart">';
    const colors = ['primary', 'success', 'warning', 'info', 'danger'];
    day.keywords.forEach((kw, i) => {{
      const pct = (kw.count / maxKw * 100).toFixed(0);
      const pctTotal = (kw.count / day.total * 100).toFixed(1);
      html += `
        <div class="bar-item">
          <span class="bar-label">${{kw.name}}</span>
          <div class="bar-track">
            <div class="bar-fill ${{colors[i % colors.length]}}" style="width:${{pct}}%"></div>
          </div>
          <span class="bar-count">${{kw.count}} (${{pctTotal}}%)</span>
        </div>
      `;
    }});
    html += '</div>';

    // Notes table
    html += '<h4 style="font-size:14px;font-weight:600;margin:16px 0 10px;">笔记 TOP 8</h4>';
    html += '<table class="data-table"><thead><tr><th style="width:40px">排名</th><th>笔记标题</th><th style="width:80px">回复数</th></tr></thead><tbody>';
    day.notes_top.forEach((note, i) => {{
      const rankClass = i < 3 ? 'rank-' + (i+1) : 'rank-other';
      const title = note.name.length > 30 ? note.name.substring(0, 27) + '...' : note.name;
      html += `<tr><td><span class="rank ${{rankClass}}">${{i+1}}</span></td><td>${{title || '<span style="color:#94a3b8">未命名笔记</span>'}}</td><td><strong>${{note.count}}</strong></td></tr>`;
    }});
    html += '</tbody></table>';

    // Users table
    html += '<h4 style="font-size:14px;font-weight:600;margin:16px 0 10px;">活跃用户 TOP 8</h4>';
    html += '<table class="data-table"><thead><tr><th style="width:40px">排名</th><th>用户名</th><th style="width:80px">回复数</th></tr></thead><tbody>';
    day.users_top.forEach((user, i) => {{
      const rankClass = i < 3 ? 'rank-' + (i+1) : 'rank-other';
      html += `<tr><td><span class="rank ${{rankClass}}">${{i+1}}</span></td><td>${{user.name}}</td><td><strong>${{user.count}}</strong></td></tr>`;
    }});
    html += '</tbody></table>';

    div.innerHTML = html;
    contentsEl.appendChild(div);
  }});
}}

function switchTab(idx) {{
  document.querySelectorAll('.tab-btn').forEach((btn, i) => {{
    btn.classList.toggle('active', i === idx);
  }});
  document.querySelectorAll('.tab-content').forEach((c, i) => {{
    c.classList.toggle('active', i === idx);
  }});
}}

// 渲染全局图表
function renderGlobalCharts() {{
  const colors = ['primary', 'success', 'warning', 'info', 'danger'];

  function renderBarChart(containerId, data, total) {{
    const container = document.getElementById(containerId);
    const maxVal = data.length > 0 ? data[0].count : 1;
    let html = '';
    data.forEach((item, i) => {{
      const pct = (item.count / maxVal * 100).toFixed(0);
      const pctTotal = (item.count / total * 100).toFixed(1);
      const name = item.name.length > 20 ? item.name.substring(0, 17) + '...' : item.name;
      html += `
        <div class="bar-item">
          <span class="bar-label">${{name || '<span style="color:#94a3b8">未命名</span>'}}</span>
          <div class="bar-track">
            <div class="bar-fill ${{colors[i % colors.length]}}" style="width:${{pct}}%"></div>
          </div>
          <span class="bar-count">${{item.count}} (${{pctTotal}}%)</span>
        </div>
      `;
    }});
    container.innerHTML = html;
  }}

  renderBarChart('globalKeywords', globalKeywords, {total_all});
  renderBarChart('globalNotes', globalNotes, {total_all});
}}

// Init
document.addEventListener('DOMContentLoaded', () => {{
  drawTrendChart();
  renderTabs();
  renderGlobalCharts();
}});
window.addEventListener('resize', drawTrendChart);
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"HTML 报告已生成：{output_path}")


def print_report(daily: dict):
    """打印按日统计报告到控制台。"""
    if not daily:
        print("未找到任何数据。")
        return

    sorted_dates = sorted(daily.keys())
    total_all = sum(d["total"] for d in daily.values())
    total_notes = len(set().union(*(d["note_ids"] for d in daily.values())))
    total_users = len(set().union(*(d["user_ids"] for d in daily.values())))

    print("=" * 70)
    print("  评论回复统计报告")
    print("=" * 70)
    print(f"  数据范围: {sorted_dates[0]} ~ {sorted_dates[-1]}")
    print(f"  总回复数: {total_all}")
    print(f"  涉及笔记: {total_notes} 篇")
    print(f"  涉及用户: {total_users} 人")
    print("=" * 70)

    for date in sorted_dates:
        d = daily[date]
        print(f"\n{'─' * 70}")
        print(f"  [{date}]")
        print(f"{'─' * 70}")

        print(f"  回复总数: {d['total']}")
        print(f"  涉及笔记: {len(d['note_ids'])} 篇")
        print(f"  涉及用户: {len(d['user_ids'])} 人")

        print(f"\n  发送状态:")
        for status, count in d["status_counter"].most_common():
            pct = count / d["total"] * 100
            print(f"    {status}: {count} ({pct:.1f}%)")

        print(f"\n  关键词 TOP 5:")
        for kw, count in d["keyword_counter"].most_common(5):
            pct = count / d["total"] * 100
            bar = "█" * int(pct / 2)
            print(f"    {kw:<12} {count:>3} ({pct:>5.1f}%) {bar}")

        print(f"\n  笔记 TOP 5 (按回复数):")
        for note, count in d["note_counter"].most_common(5):
            title = note if len(note) <= 25 else note[:22] + "..."
            print(f"    {title:<28} {count:>3} 条")

        print(f"\n  活跃用户 TOP 5:")
        for user, count in d["user_counter"].most_common(5):
            print(f"    {user:<28} {count:>3} 条")

    print(f"\n{'=' * 70}")
    print("  全局洞察")
    print(f"{'=' * 70}")

    all_keywords = Counter()
    all_notes = Counter()
    all_status = Counter()
    for d in daily.values():
        all_keywords += d["keyword_counter"]
        all_notes += d["note_counter"]
        all_status += d["status_counter"]

    print(f"\n  全局关键词 TOP 10:")
    for kw, count in all_keywords.most_common(10):
        pct = count / total_all * 100
        bar = "█" * int(pct / 2)
        print(f"    {kw:<12} {count:>4} ({pct:>5.1f}%) {bar}")

    print(f"\n  全局笔记 TOP 10 (按回复数):")
    for note, count in all_notes.most_common(10):
        pct = count / total_all * 100
        title = note if len(note) <= 30 else note[:27] + "..."
        print(f"    {title:<33} {count:>4} ({pct:>5.1f}%)")

    days_count = len(sorted_dates)
    avg_per_day = total_all / days_count if days_count else 0
    print(f"\n  日均回复数: {avg_per_day:.1f}")

    max_date = max(sorted_dates, key=lambda d: daily[d]["total"])
    min_date = min(sorted_dates, key=lambda d: daily[d]["total"])
    print(f"  最高回复日: {max_date} ({daily[max_date]['total']} 条)")
    print(f"  最低回复日: {min_date} ({daily[min_date]['total']} 条)")

    success = all_status.get("success", 0)
    success_rate = success / total_all * 100 if total_all else 0
    print(f"  全局发送成功率: {success_rate:.1f}%")

    print(f"\n{'=' * 70}")


def main():
    directory = os.path.dirname(os.path.abspath(__file__))
    records = load_all_responses(directory)
    print(f"加载 {len(records)} 条记录")

    daily = compute_daily_stats(records)

    # 控制台输出
    print_report(daily)

    # 生成 HTML 报告
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(directory, f"comment_report_{timestamp}.html")
    generate_html_report(daily, output_path)


if __name__ == "__main__":
    main()
