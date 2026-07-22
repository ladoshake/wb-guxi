#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股 股息率排名（按市值分档）—— 数据抓取与计算
全部数据均通过 WorkBuddy 内置的【腾讯自选股(WeStock)】技能取数（与腾讯自选股 App / qt.gtimg.cn 同源）：
  - 股票池筛选(按市值过滤): westock-tool 的 filter
  - 实时行情(现价/总市值/TTM 股息率): westock-data 的 quote
  - 历史分红(算 LFY 口径): westock-data 的 dividend list (--years 5)
流程：池(filter, 总市值>500亿元) -> 行情(quote) -> 分红(dividend list) -> finalize_one 算 LFY。
TTM 股息率统一取行情接口的 dividend_ratio_ttm。
市值分档: 总市值分两档 >1000亿 / 500~1000亿（各档内按股息率 Top30）。
输出: data.json (供前端网页使用)。
"""
import json
import os
import time
import datetime as dt
import subprocess

WORKDIR = "/Users/green/WorkBuddy/2026-07-11-16-35-47"
OUT = f"{WORKDIR}/data.json"
TODAY = dt.date.today()
TTM_START = TODAY - dt.timedelta(days=365)
TIERS = [
    ("gt1000", 1000.0, lambda mv: mv > 1000.0),
    ("mid500", 500.0, lambda mv: 500.0 < mv <= 1000.0),
]

# 股息率合理性护栏
MAX_YIELD = 30.0


def _safe_yield(per10, price):
    """per10=每股分红×10(元)；price=现价。返回股息率(%)；>MAX_YIELD 视为异常返回 0。"""
    if not price or price <= 0:
        return 0.0
    y = per10 / 10.0 / price * 100.0
    return y if y <= MAX_YIELD else 0.0


# ------------------------- 腾讯自选股 WeStock 技能 -------------------------
WESTOCK_DIR = "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources/builtin-skills"
WESTOCK_TOOL = os.path.join(WESTOCK_DIR, "westock-tool", "scripts", "index.js")
WESTOCK_DATA = os.path.join(WESTOCK_DIR, "westock-data", "scripts", "index.js")
WESTOCK_NODE = "/Users/green/.workbuddy/binaries/node/versions/22.22.2/bin/node"

# 市值筛选阈值：>500亿元（raw 元 = 5e10）
WESTOCK_MV_FLOOR = 50000000000


def run_westock(script, *args, retries=2):
    """调用 WeStock 技能脚本(node)，返回解析后的 JSON（--raw）。失败/超时速重试。"""
    cmd = [WESTOCK_NODE, script, *args, "--raw"]
    for attempt in range(retries + 1):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            out = (p.stdout or "").strip()
            if not out:
                if attempt < retries:
                    time.sleep(1.0)
                    continue
                return None
            return json.loads(out)
        except Exception as e:
            if attempt < retries:
                time.sleep(1.0)
                continue
            print(f"  [westock] {script} {args[:2]} 失败: {e}")
            return None
    return None


def westock_pool():
    """市值>500亿元 的 [code, name] 列表（A股是腾讯自选股默认市场，不加 --market）。
    code 形如 sh600519(沪) / sz000651(深)。"""
    data = run_westock(WESTOCK_TOOL, "filter",
                       f"intersect([TotalMV > {WESTOCK_MV_FLOOR}])", "--limit", "5000")
    if not isinstance(data, list):
        return []
    out = []
    for x in data:
        code = (x.get("code") or "").strip()
        name = (x.get("name") or "").strip()
        if code:
            out.append((code, name))
    return out


def westock_quotes(codes):
    """批量行情：返回 {code: quote_dict}。A股响应结构为 {symbol, data:{}}。"""
    result = {}
    for i in range(0, len(codes), 50):
        chunk = codes[i:i + 50]
        data = run_westock(WESTOCK_DATA, "quote", ",".join(chunk))
        if not data:
            continue
        items = data.get("data", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            q = it.get("data", it)
            if not isinstance(q, dict):
                continue
            code = q.get("code") or q.get("symbol") or it.get("symbol") or it.get("code")
            if code:
                result[code] = q
        time.sleep(0.2)
    return result


def _extract_divs(data):
    """从分红响应中提取分红记录列表。单只查询返回扁平列表 [{exDiviDate, cashDiviRMB, ...}]。"""
    if not data:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "sections" in data:
        secs = data["sections"]
        if isinstance(secs, list) and secs and isinstance(secs[0], list):
            return secs[0]
        return secs if isinstance(secs, list) else []
    return []


def westock_dividends(codes):
    """逐只分红(--years 5)：返回 {code: [record,...]}。
    注意：批量 dividend list 的 sections 顺序与请求代码顺序不保证一致，且每条记录内无 code 字段，
    无法自识别归属；故必须逐只查询才能保证分红正确归到对应股票（单只返回扁平列表，可靠）。"""
    result = {}
    for i, c in enumerate(codes):
        d = run_westock(WESTOCK_DATA, "dividend", "list", c, "--years", "5")
        result[c] = _extract_divs(d)
        if (i + 1) % 100 == 0:
            print(f"     [div] 已处理 {i + 1}/{len(codes)}")
        time.sleep(0.1)
    return result


def _to_iso(s):
    """YYYYMMDD -> YYYY-MM-DD；已是 ISO 或空则原样返回。"""
    s = str(s or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _div_to_rows(divs):
    """A股分红记录 -> finalize_one 行格式。以 reportEndDate(财年) 归并；
    cashDiviRMB 已是『元/10股』(如 茅台 2025: 10派280.24元 -> cashDiviRMB≈280.24)，故 per10 直接取该值。"""
    rows = []
    for d in (divs or []):
        ex = _to_iso(d.get("exDiviDate"))
        fy = d.get("reportEndDate")
        fy = int(fy[:4]) if isinstance(fy, str) and len(fy) >= 4 else None
        try:
            per = float(d.get("cashDiviRMB") or 0)
        except Exception:
            per = 0.0
        if per <= 0:
            continue
        rows.append({"ex_date": ex, "fy": fy, "per10": round(per, 4), "type": "Cash"})
    return rows


# ------------------------- A股构建 -------------------------
def build():
    print("[A股] 市值筛选股票池(westock-tool filter, 总市值>500亿元) ...")
    pool = westock_pool()
    codes = [c for c, _ in pool]
    name_map = {c: n for c, n in pool}
    print(f"     A股候选(>500亿元): {len(codes)}")
    quotes = westock_quotes(codes)
    divs_map = westock_dividends(codes)
    raw, done = [], 0
    for code in codes:
        q = quotes.get(code)
        if not q:
            continue
        try:
            price = float(q.get("price") or 0)
            mv_raw = float(q.get("total_market_cap") or 0)  # 元 raw
        except Exception:
            continue
        if price <= 0 or mv_raw <= 0:
            continue
        mv_yi = mv_raw                                 # total_market_cap 已是亿元
        rows = _div_to_rows(divs_map.get(code, []))
        rec = finalize_one(code, name_map.get(code, code), price, mv_yi, rows)
        # TTM 股息率以行情接口为准(dividend_ratio_ttm)
        try:
            ttm = float(q.get("dividend_ratio_ttm") or 0)
        except Exception:
            ttm = 0.0
        if ttm > 0:
            rec["ttm_yield"] = round(ttm, 3)
        raw.append(rec)
        done += 1
        if done % 30 == 0:
            print(f"     [A] 已处理 {done}/{len(codes)}")
    return raw


# ------------------------- 通用：计算 TTM/LFY + 分档排名 -------------------------
def finalize_one(code, name, price, mv_yi, rows):
    """rows: [{ex_date, fy, per10(元/10股), type}]；A股按归属财年归并 LFY。"""
    def to_date(s):
        try:
            return dt.date.fromisoformat(s)
        except Exception:
            return None
    ttm_rows = [x for x in rows if (lambda d: d and TTM_START <= d <= TODAY)(to_date(x.get("ex_date", "")))]
    ttm_per10 = sum(x["per10"] for x in ttm_rows)
    ttm_div_count = sum(1 for x in ttm_rows if x["per10"] > 0)
    fy_years = sorted({x["fy"] for x in rows if isinstance(x.get("fy"), int)}, reverse=True)
    lfy_year = fy_years[0] if fy_years else ""
    lfy_per10 = sum(x["per10"] for x in rows if x["fy"] == lfy_year) if isinstance(lfy_year, int) else 0.0
    lfy_div_count = sum(1 for x in rows if x["fy"] == lfy_year and x["per10"] > 0) if isinstance(lfy_year, int) else 0
    prev_year = (lfy_year - 1) if isinstance(lfy_year, int) else ""
    prev2_year = (lfy_year - 2) if isinstance(lfy_year, int) else ""
    prev_per10 = sum(x["per10"] for x in rows if x["fy"] == prev_year) if isinstance(lfy_year, int) else 0.0
    prev2_per10 = sum(x["per10"] for x in rows if x["fy"] == prev2_year) if isinstance(lfy_year, int) else 0.0
    ttm_yield = _safe_yield(ttm_per10, price)
    lfy_yield = _safe_yield(lfy_per10, price)
    prev_yield = _safe_yield(prev_per10, price)
    prev2_yield = _safe_yield(prev2_per10, price)
    return {
        "code": code, "name": name, "ex_tag": "",
        "price": round(price, 2), "total_mv_yi": round(mv_yi, 2),
        "ttm_per10": round(ttm_per10, 4), "ttm_yield": round(ttm_yield, 3), "ttm_div_count": ttm_div_count,
        "lfy_year": lfy_year, "lfy_per10": round(lfy_per10, 4), "lfy_yield": round(lfy_yield, 3), "lfy_div_count": lfy_div_count,
        "prev_year": prev_year, "prev_per10": round(prev_per10, 4), "prev_yield": round(prev_yield, 3),
        "prev2_year": prev2_year, "prev2_per10": round(prev2_per10, 4), "prev2_yield": round(prev2_yield, 3),
    }


def make_market(records):
    """按总市值(亿元)分档并各取 Top30。"""
    tiers_out = []
    for key, thresh, pred in TIERS:
        sub = [r for r in records if pred(r["total_mv_yi"])]
        ttm_rank = sorted([r for r in sub if r["ttm_yield"] > 0], key=lambda x: x["ttm_yield"], reverse=True)[:30]
        lfy_rank = sorted([r for r in sub if r["lfy_yield"] > 0], key=lambda x: x["lfy_yield"], reverse=True)[:30]
        if key == "gt1000":
            label = f"市值 > {thresh:.0f}亿元"
        else:
            label = f"{thresh:.0f}亿元 ≥ 市值 > 500亿元"
        tiers_out.append({"key": key, "label": label, "count": len(sub),
                          "ttm_rank": ttm_rank, "lfy_rank": lfy_rank})
    return tiers_out


# ------------------------- 主流程 -------------------------
def main():
    a_raw = build()
    out = {
        "generated_at": TODAY.isoformat(),
        "ttm_start": TTM_START.isoformat(),
        "tiers": make_market(a_raw),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    for t in out["tiers"]:
        print(f"  [{t['label']}] 公司数={t['count']}")
    print(f"      写入 {OUT}")


if __name__ == "__main__":
    main()
