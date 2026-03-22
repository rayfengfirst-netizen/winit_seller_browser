"""
库存查看器 / 报表页共用视觉主题（CSS 片段，嵌入 HTML）。
"""

from __future__ import annotations

# 与 inventory_viewer、winit_no_sales_report 内联样式配合使用
VIEWER_THEME_CSS = """
:root {
  --bg: #e8eef4;
  --surface: #ffffff;
  --text: #0f172a;
  --muted: #64748b;
  --border: #cbd5e1;
  --accent: #0d9488;
  --accent-dark: #0f766e;
  --accent-soft: #ccfbf1;
  --accent2: #2563eb;
  --accent2-soft: #dbeafe;
  --warn: #d97706;
  --warn-soft: #fef3c7;
  --radius: 12px;
  --shadow: 0 4px 14px rgba(15, 23, 42, 0.08);
}
body {
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, "PingFang SC", sans-serif;
  margin: 0;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
  min-height: 100vh;
}
.page {
  max-width: 1180px;
  margin: 0 auto;
  padding: 1rem 1.25rem 2.5rem;
}
.banner {
  background: linear-gradient(135deg, var(--accent-dark) 0%, #0e7490 55%, var(--accent2) 100%);
  color: #fff;
  border-radius: var(--radius);
  padding: 1.25rem 1.5rem;
  margin-bottom: 1.25rem;
  box-shadow: var(--shadow);
}
.banner h1 {
  margin: 0 0 0.35rem 0;
  font-size: 1.5rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}
.banner .sub {
  margin: 0;
  opacity: 0.92;
  font-size: 0.9rem;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
  align-items: center;
  margin-bottom: 1.25rem;
}
.toolbar a {
  display: inline-block;
  padding: 0.45rem 0.9rem;
  border-radius: 8px;
  font-weight: 600;
  font-size: 0.875rem;
  text-decoration: none;
  background: var(--surface);
  color: var(--accent-dark);
  border: 1px solid var(--border);
  box-shadow: 0 1px 2px rgba(0,0,0,.04);
}
.toolbar a:hover { background: var(--accent-soft); border-color: var(--accent); }
.toolbar a.primary {
  background: var(--accent2-soft);
  color: var(--accent2);
  border-color: #93c5fd;
}
.toolbar a.primary:hover { background: #bfdbfe; }
.muted { color: var(--muted); font-size: 0.9rem; }
code { background: var(--accent-soft); color: var(--accent-dark); padding: 0.1em 0.35em; border-radius: 4px; font-size: 0.85em; }
.card {
  background: var(--surface);
  border-radius: var(--radius);
  border: 1px solid var(--border);
  box-shadow: var(--shadow);
  padding: 1rem 1.25rem 1.25rem;
  margin-bottom: 1.25rem;
}
.card h2 {
  margin: 0 0 0.75rem 0;
  font-size: 1.1rem;
  color: var(--accent-dark);
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--accent-soft);
}
table.data {
  border-collapse: collapse;
  width: 100%;
  font-size: 13px;
}
table.data th,
table.data td {
  border: 1px solid var(--border);
  padding: 8px 10px;
  text-align: left;
}
table.data thead th {
  background: linear-gradient(180deg, #f1f5f9 0%, #e2e8f0 100%);
  color: #334155;
  font-weight: 600;
}
table.data tbody tr:nth-child(even) { background: #f8fafc; }
table.data tbody tr:hover { background: #ecfeff; }
td.num, th.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: var(--accent-dark);
}
.note-strip {
  background: var(--warn-soft);
  border: 1px solid #fcd34d;
  border-radius: 8px;
  padding: 0.65rem 1rem;
  margin-bottom: 1rem;
  font-size: 0.88rem;
  color: #92400e;
}
h2.section-title {
  font-size: 0.82rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  margin: 0 0 0.75rem 0;
  font-weight: 700;
}
.stat-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
  margin: 0 0 0.75rem 0;
}
.stat-pill {
  display: inline-block;
  background: var(--accent-soft);
  color: var(--accent-dark);
  padding: 0.3rem 0.65rem;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.stat-pill.blue {
  background: var(--accent2-soft);
  color: #1d4ed8;
}
"""
