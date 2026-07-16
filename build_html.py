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
  .desc{margin:0;color:var(--sub);font-size:13px}
  .box{display:flex;align-items:baseline;gap:6px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px 12px;white-space:nowrap}
  .num{font-size:13px;font-weight:700;color:var(--brand2)}
  .lab{font-size:13px;color:var(--sub)}
  .right{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .btn{padding:6px 14px;border:1px solid var(--brand2);background:var(--brand2);color:#fff;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
  .btn:hover{opacity:.9}
  .btn:disabled{opacity:.6;cursor:default}
  .tabs{display:flex;gap:8px;margin:14px 0 8px}
  .tab{padding:9px 18px;border:1px solid var(--line);border-radius:999px;background:var(--card);cursor:pointer;font-size:14px;font-weight:600;color:var(--sub)}
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
  .note{margin-top:28px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;font-size:13px;color:#374151}
  .note h3{margin:0 0 8px;font-size:15px}
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
      <div class="right">
        <div class="box"><div class="num" id="cnt"></div><div class="lab">市值&gt;1000亿公司数</div></div>
        <button id="refresh" class="btn">刷新数据</button>
      </div>
    </div>
  </header>

  <div class="tabs">
    <div class="tab active" data-tab="ttm">TTM 股息率排名（近12个月）</div>
    <div class="tab alt" data-tab="lfy">LFY 股息率排名（最近财年）</div>
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
      <li><b>市值筛选</b>：以腾讯行情接口的<b>总市值</b>（单位：亿元人民币）为准，取大于 1000 亿的公司。</li>
      <li><b>股息率（自行计算）</b>：股息率 = 现金分红合计数 ÷ 当前总市值 × 100%。等价地，按每股现金分红 ÷ 当前股价 计算，结果一致。</li>
      <li><b>TTM 股息率</b>：取<b>除权除息日</b>在最近 12 个月内（含本次生成日）的所有现金分红之和 ÷ 当前总市值。可涵盖上年年报分红与当年中期分红。</li>
      <li><b>LFY 股息率</b>：取<b>最近一个完整财年</b>（按分红年度，通常为最新披露的年度报告所属年度）的<b>全部现金分红之和</b> ÷ 当前总市值。<b>一年内若有多次分红（年度 / 中期 / 特别），会全部相加</b>。</li>
      <li><b>每股分红列</b>：单位为 <b>元/股</b>。TTM 表为该列近 12 个月现金分红合计数；LFY 表为该财年现金分红合计数（含年度/中期/特别分红）。</li>
      <li><b>历史股息率列（LFY 表）</b>：在「LFY 股息率」之后额外列出<b>前一年（LFY 财年−1）与前两年（LFY 财年−2）股息率</b>，计算口径与 LFY 完全一致（该财年内多次分红相加 ÷ 总市值）。表头直接显示具体年份，单元格下方小字为该财年年份。可用于观察分红的连续性与趋势。</li>
      <li><b>数据来源</b>：市值来自腾讯财经实时行情（总市值含 H 股）；分红明细来自公开财经数据接口（巨潮历史分红）。榜单为计算快照，非投资建议。</li>
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

// 加载数据：通过服务器打开时优先取最新 /api/data，否则用内嵌数据（双击文件场景）
(async function(){
  if(location.protocol.startsWith("http")){
    try{
      const res = await fetch("/api/data");
      if(res.ok){ initData(await res.json()); return; }
    }catch(e){ /* 回退内嵌数据 */ }
  }
  initData(EMBEDDED);
})();

// 刷新按钮：仅在服务器环境可用，点击后重跑抓取+生成并整页重载
const refreshBtn = document.getElementById("refresh");
refreshBtn.addEventListener("click", async ()=>{
  if(!location.protocol.startsWith("http")){
    alert("请通过本地服务器打开页面以使用刷新功能：运行 python serve.py 后访问 http://localhost:8000");
    return;
  }
  const old = refreshBtn.textContent;
  refreshBtn.disabled = true;
  refreshBtn.textContent = "刷新中…";
  try{
    const r = await fetch("/api/refresh", {method:"POST"});
    if(!r.ok) throw new Error("服务器返回 " + r.status);
    // 服务端已重新生成 index.html，整页重载以获取最新表头与数据
    location.reload();
  }catch(e){
    alert("刷新失败：" + e.message);
    refreshBtn.disabled = false;
    refreshBtn.textContent = old;
  }
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    build()
