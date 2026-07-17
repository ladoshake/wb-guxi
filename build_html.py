#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取 data.json，生成自包含的交互式网页 index.html（数据内嵌，可直接打开）。"""
import json

WORKDIR = "/Users/green/WorkBuddy/2026-07-11-16-35-47"
DATA = f"{WORKDIR}/data.json"
OUT = f"{WORKDIR}/index.html"


def rank_rows(rows, kind):
    """kind: 'ttm' 或 'lfy'，明确取对应字段，避免记录中同时含两套字段时取错。"""
    per10_key = "ttm_per10" if kind == "ttm" else "lfy_per10"
    yield_key = "ttm_yield" if kind == "ttm" else "lfy_yield"
    out = []
    for i, r in enumerate(rows, 1):
        row = {
            "rank": i,
            "code": r["code"],
            "name": r["name"],
            "price": r["price"],
            "total_mv_yi": r["total_mv_yi"],
            "dps": round((r.get(per10_key, 0) or 0) / 10.0, 3),  # 元/股
            "yield": r[yield_key],
            "fy": r.get("lfy_year", "") if kind == "lfy" else "",
        }
        if kind == "lfy":
            # 前年 / 大前年股息率(相对 LFY 财年, 口径同 LFY)
            row["prev_yield"] = round(r.get("prev_yield", 0) or 0, 3)
            row["prev2_yield"] = round(r.get("prev2_yield", 0) or 0, 3)
            row["prev_year"] = r.get("prev_year", "")
            row["prev2_year"] = r.get("prev2_year", "")
        out.append(row)
    return out


def build():
    with open(DATA, encoding="utf-8") as f:
        data = json.load(f)

    ttm = rank_rows(data["ttm_rank"], "ttm")
    lfy = rank_rows(data["lfy_rank"], "lfy")

    # 表头年份：取 LFY 榜「最近财年」的众数，表头直接显示具体年份
    years = [int(r["fy"]) for r in lfy if r.get("fy")]
    modal = max(set(years), key=years.count) if years else None
    prev_hdr = f"{modal - 1}年股息率" if modal is not None else "前年股息率"
    prev2_hdr = f"{modal - 2}年股息率" if modal is not None else "大前年股息率"

    payload = {
        "generated_at": data["generated_at"],
        "market_cap_min_yi": data["market_cap_min_yi"],
        "big_cap_count": data["big_cap_count"],
        "ttm_start": data["ttm_start"],
        "ttm": ttm,
        "lfy": lfy,
    }

    html = HTML_TEMPLATE
    html = html.replace("/*__DATA__*/", json.dumps(payload, ensure_ascii=False))
    html = html.replace("/*__PREV_HDR__*/", prev_hdr)
    html = html.replace("/*__PREV2_HDR__*/", prev2_hdr)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[html] 生成 {OUT}  TTM={len(ttm)} LFY={len(lfy)} | 表头: {prev_hdr} / {prev2_hdr}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股股息率排名</title>
<style>
  :root{
    --bg:#f5f6f8; --card:#ffffff; --ink:#1f2430; --sub:#6b7280;
    --line:#e5e7eb; --brand:#c0392b; --brand2:#2563eb; --gold:#b8860b;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       background:var(--bg);color:var(--ink);line-height:1.5}
  .wrap{max-width:1100px;margin:0 auto;padding:16px 16px 56px}
  header h1{font-size:21px;margin:0 0 4px}
  header p{margin:1px 0;color:var(--sub);font-size:13px}
  .subhead{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin:8px 0 2px}
  .desc{margin:0;color:var(--sub);font-size:clamp(12px,1.1vw,14px);line-height:1.5}
  .box{display:flex;align-items:baseline;gap:6px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px 12px;white-space:nowrap}
  .num{font-size:13px;font-weight:700;color:var(--brand2)}
  .lab{font-size:13px;color:var(--sub)}
  .tabs{display:flex;gap:8px;margin:14px 0 8px}
  .tab{padding:7px 20px;display:flex;flex-direction:column;align-items:center;gap:1px;border:1px solid var(--line);border-radius:999px;background:var(--card);cursor:pointer;font-size:14px;font-weight:600;color:var(--sub);line-height:1.2}
  .tab-main{white-space:nowrap}
  .tab-sub{font-size:11px;font-weight:400;opacity:.78}
  .tab.active .tab-sub{opacity:.92}
  .tab.active{background:var(--brand2);color:#fff;border-color:var(--brand2)}
  .tab.alt.active{background:var(--brand2);border-color:var(--brand2)}
  .panel{display:none}
  .panel.active{display:block}
  table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13.5px}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
  th{background:#f0f1f4;color:#374151;font-weight:600;cursor:pointer;user-select:none;position:relative}
  th.sort-asc::after{content:" ▲";font-size:10px;color:var(--brand)}
  th.sort-desc::after{content:" ▼";font-size:10px;color:var(--brand)}
  tbody tr:nth-child(even){background:#f7f8fa}
  tbody tr:hover{background:#e8f0fe}
  .rk{display:inline-block;min-width:22px;text-align:center;font-weight:700;color:var(--brand2)}
  .code{color:var(--sub);font-size:12px}
  .yld{font-weight:700;color:var(--brand)}
  .panel.alt .yld{color:var(--brand2)}
  .yld2{font-weight:600;color:#0f766e}
  .sub{font-size:10px;color:var(--sub);font-weight:400;line-height:1.15}
  .note{margin-top:28px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:clamp(12px,1.6vw,18px) clamp(14px,1.8vw,20px);font-size:clamp(12px,1.1vw,14px);color:#374151}
  .note h3{margin:0 0 8px;font-size:clamp(14px,1.4vw,16px)}
  .note ul{margin:6px 0;padding-left:20px}
  .note li{margin:3px 0}
  .tag{display:inline-block;background:#eef2ff;color:#4338ca;border-radius:6px;padding:1px 7px;font-size:11px;margin-left:6px}
  footer{margin-top:24px;text-align:center;color:var(--sub);font-size:12px}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>A股股息率排名</h1>
    <div class="subhead">
      <p class="desc">A股总市值&gt;1000亿元公司，按股息率排名取前30。生成日期：<span id="gen"></span> ｜ TTM窗口起点：<span id="ttmstart"></span></p>
      <div class="box"><div class="num" id="cnt"></div><div class="lab">市值&gt;1000亿公司数</div></div>
    </div>
  </header>

  <div class="tabs">
    <div class="tab active" data-tab="ttm"><span class="tab-main">TTM 股息率</span><span class="tab-sub">最近12个月</span></div>
    <div class="tab alt" data-tab="lfy"><span class="tab-main">LFY 股息率</span><span class="tab-sub">最近财年</span></div>
  </div>

  <div class="panel active" id="panel-ttm">
    <table id="table-ttm">
      <thead><tr>
        <th data-k="rank">排名</th><th data-k="name">名称</th><th data-k="code">代码</th>
        <th data-k="price">现价(元)</th><th data-k="total_mv_yi">总市值(亿)</th>
        <th data-k="dps">每股分红(元/股)</th><th data-k="yield">TTM股息率</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>

  <div class="panel" id="panel-lfy">
    <table id="table-lfy">
      <thead><tr>
        <th data-k="rank">排名</th><th data-k="name">名称</th><th data-k="code">代码</th>
        <th data-k="price">现价(元)</th><th data-k="total_mv_yi">总市值(亿)</th>
        <th data-k="dps">每股分红(元/股)</th><th data-k="yield">LFY股息率</th><th data-k="prev_yield">/*__PREV_HDR__*/</th><th data-k="prev2_yield">/*__PREV2_HDR__*/</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>

  <div class="note">
    <h3>计算口径说明</h3>
    <ul>
      <li><b>市值筛选</b>：按总市值取大于 1000 亿元的公司。</li>
      <li><b>股息率</b>：现金分红合计 ÷ 当前总市值 × 100%。</li>
      <li><b>TTM</b>：除权除息日在近 12 个月内的现金分红之和。</li>
      <li><b>LFY</b>：最近一个完整财年的现金分红之和（一年内多次分红全部相加）。</li>
      <li><b>历史列（LFY 表）</b>：LFY 财年前一年、前两年的股息率，口径同 LFY，表头标注具体年份。</li>
      <li><b>数据来源</b>：市值来自腾讯财经实时行情，分红明细来自巨潮历史分红。榜单为计算快照，非投资建议。</li>
    </ul>
  </div>

  <footer>股息率排名工具 · 数据由脚本抓取并计算生成 · 仅供研究参考，不构成投资建议</footer>
</div>

<script>
const EMBEDDED = /*__DATA__*/;
let DATA = EMBEDDED;

const fmt = (n,d=2)=> (n==null||isNaN(n))?"-":Number(n).toLocaleString("zh-CN",{minimumFractionDigits:d,maximumFractionDigits:d});

// 排序状态：每个表独立维护，便于刷新后保留用户排序
const sortState = { ttm:{key:"yield",dir:-1}, lfy:{key:"yield",dir:-1} };

function renderTable(tableId, rows, withFy){
  const tbody = document.querySelector(`#${tableId} tbody`);
  tbody.innerHTML = "";
  rows.forEach(r=>{
    const tr = document.createElement("tr");
    const lfyCell = `<td class="yld">${fmt(r.yield,2)}%</td>`;
    const prev = withFy ? `<td class="yld2">${fmt(r.prev_yield,2)}%</td><td class="yld2">${fmt(r.prev2_yield,2)}%</td>` : "";
    tr.innerHTML = `<td><span class="rk">${r.rank}</span></td>
      <td><b>${r.name}</b></td>
      <td class="code">${r.code}</td>
      <td>${fmt(r.price)}</td>
      <td>${fmt(r.total_mv_yi,0)}</td>
      <td>${fmt(r.dps,3)}</td>
      ${lfyCell}${prev}`;
    tbody.appendChild(tr);
  });
}

function applyTable(tableId, withFy){
  const key = withFy ? "lfy" : "ttm";
  const rows = (withFy ? DATA.lfy : DATA.ttm).slice();
  const sk = sortState[key];
  rows.sort((a,b)=>{
    let va=a[sk.key], vb=b[sk.key];
    if(typeof va==="string"){ return sk.dir*va.localeCompare(vb,"zh"); }
    return sk.dir*(va-vb);
  });
  renderTable(tableId, rows, withFy);
}

function applyAll(){ applyTable("table-ttm", false); applyTable("table-lfy", true); }

function bindTable(tableId, withFy){
  const table = document.getElementById(tableId);
  table.querySelectorAll("th").forEach(th=>{
    th.addEventListener("click",()=>{
      const key = withFy ? "lfy" : "ttm";
      const kp = th.dataset.k;
      if(sortState[key].key===kp){ sortState[key].dir *= -1; }
      else { sortState[key].key=kp; sortState[key].dir = (kp==="rank"||kp==="name"||kp==="code")?1:-1; }
      table.querySelectorAll("th").forEach(x=>x.classList.remove("sort-asc","sort-desc"));
      th.classList.add(sortState[key].dir===1?"sort-asc":"sort-desc");
      applyTable(tableId, withFy);
    });
  });
}

function initData(d){
  DATA = d;
  document.getElementById("gen").textContent = DATA.generated_at;
  document.getElementById("ttmstart").textContent = DATA.ttm_start;
  document.getElementById("cnt").textContent = DATA.big_cap_count;
  applyAll();
}

// 一次性绑定表头排序
bindTable("table-ttm", false);
bindTable("table-lfy", true);

// tab 切换
document.querySelectorAll(".tab").forEach(t=>{
  t.addEventListener("click",()=>{
    document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(x=>x.classList.remove("active"));
    t.classList.add("active");
    document.getElementById("panel-"+t.dataset.tab).classList.add("active");
  });
});

// 直接使用内嵌数据渲染（自包含，可直接打开或静态部署）
initData(EMBEDDED);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    build()
