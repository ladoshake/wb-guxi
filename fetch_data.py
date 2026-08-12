#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股 股息率排名（按市值分档）—— 数据抓取与计算
市值/现价来自腾讯 gtimg（与腾讯自选股 / WeStock 同源），历史分红来自巨潮资讯(cninfo)。
流程：A股代码列表(akshare) -> 批量行情(gtimg, 总市值>500亿元) -> 历史分红(cninfo) -> 算 TTM/LFY。
TTM 股息率完全由本地分红记录计算（每股分红合计 ÷ 现价 × 100%）。
市值分档: 总市值分两档 >1000亿 / 500~1000亿（各档内按股息率 Top30）。
输出: data.json (供前端网页使用)。
"""
import json
import os
import re
import time
import signal
import datetime as dt
import subprocess
import requests
import akshare as ak

os.environ.setdefault("TQDM_DISABLE", "1")

WORKDIR = "/Users/green/WorkBuddy/2026-07-11-16-35-47"
OUT = f"{WORKDIR}/data.json"
TODAY = dt.date.today()
TTM_START = TODAY - dt.timedelta(days=365)
TIERS = [
    ("gt1000", 1000.0, lambda mv: mv > 1000.0),
    ("mid500", 500.0, lambda mv: 500.0 < mv <= 1000.0),
]
TIER_LABELS = {
    "gt1000": "市值 > 1000亿元",
    "mid500": "1000亿元 ≥ 市值 > 500亿元",
}

# 股息率合理性护栏
MAX_YIELD = 30.0


def _safe_yield(per10, price):
    """per10=每股分红×10(元)；price=现价。返回股息率(%)；>MAX_YIELD 视为异常返回 0。"""
    if not price or price <= 0:
        return 0.0
    y = per10 / 10.0 / price * 100.0
    return y if y <= MAX_YIELD else 0.0


# ------------------------- 腾讯自选股 WeStock 技能（旧版内置 CLI） -------------------------
WESTOCK_DIR = "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources/builtin-skills"
WESTOCK_TOOL = os.path.join(WESTOCK_DIR, "westock-tool", "scripts", "index.js")
WESTOCK_DATA = os.path.join(WESTOCK_DIR, "westock-data", "scripts", "index.js")
WESTOCK_NODE = "/Users/green/.workbuddy/binaries/node/versions/22.22.2/bin/node"
USE_BUILTIN = os.path.isdir(WESTOCK_DIR) and os.path.isfile(WESTOCK_TOOL) and os.path.isfile(WESTOCK_DATA)

# 市值筛选阈值：>500亿元（raw 元 = 5e10）
WESTOCK_MV_FLOOR = 50000000000


def run_westock(script, *args, retries=2):
    """调用 WeStock 技能脚本(node)，返回解析后的 JSON（--raw）。失败/超时速重试。"""
    cmd = [WESTOCK_NODE, script, *args, "--raw"]
    for attempt in range(retries + 1):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                               start_new_session=True)
            out = (p.stdout or "").strip()
            if not out:
                if attempt < retries:
                    time.sleep(1.0)
                    continue
                return None
            return json.loads(out)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                pass
            if attempt < retries:
                time.sleep(1.0)
                continue
            print(f"  [westock] {script} {args[:2]} 超时(120s×{retries+1}次)")
            return None
        except Exception as e:
            if attempt < retries:
                time.sleep(1.0)
                continue
            print(f"  [westock] {script} {args[:2]} 失败: {e}")
            return None
    return None


def westock_pool():
    """市值>500亿元 的股票列表，含 filter 直接返回的 TotalMV(亿元) 和 ClosePrice(现价)。"""
    data = run_westock(WESTOCK_TOOL, "filter",
                       f"intersect([TotalMV > {WESTOCK_MV_FLOOR}])", "--limit", "5000")
    if not isinstance(data, list):
        return []
    out = []
    for x in data:
        code = (x.get("code") or "").strip()
        name = (x.get("name") or "").strip()
        if not code:
            continue
        try:
            mv = float(x.get("TotalMV") or 0)
        except Exception:
            mv = 0.0
        # TotalMV 单位归一：腾讯接口有时返回『元』原始值(>1e8)，有时返回『亿元』；
        # 以 1e8 为阈值稳健归一为亿元。
        if mv > 1e8:
            mv = mv / 1e8
        try:
            price = float(x.get("ClosePrice") or 0)
        except Exception:
            price = 0.0
        out.append((code, name, mv, price))
    return out


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
    """逐只分红(--years 5)：返回 {code: [record,...]}。"""
    result = {}
    for i, c in enumerate(codes):
        d = run_westock(WESTOCK_DATA, "dividend", "list", c, "--years", "5")
        result[c] = _extract_divs(d)
        if (i + 1) % 100 == 0:
            print(f"     [div] 已处理 {i + 1}/{len(codes)}")
        time.sleep(0.1)
    return result


# ------------------------- 新版 Fallback：akshare + 腾讯 gtimg + 巨潮 cninfo -------------------------
def _a_prefix(code):
    """A股数字代码 -> 腾讯行情前缀。"""
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    return "bj"


def akshare_pool():
    """通过 akshare 取 A股代码列表，再批量从腾讯 gtimg 取行情/总市值，返回 (>500亿, 现价>0) 的列表。"""
    df = ak.stock_info_a_code_name()
    codes = [str(c).strip() for c in df["code"].tolist()]
    out = []
    sess = requests.Session()
    batch = 600
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        gcodes = ",".join(f"{_a_prefix(c)}{c}" for c in chunk)
        url = f"http://qt.gtimg.cn/q={gcodes}"
        try:
            r = sess.get(url, timeout=60)
            r.encoding = "gbk"
            text = r.text
        except Exception as e:
            print(f"  [gtimg] 请求失败 batch {i}: {e}")
            continue
        for m in re.finditer(r'v_(\w+)="([^"]*)"', text):
            key = m.group(1)
            fields = m.group(2).split("~")
            if len(fields) < 46:
                continue
            try:
                price = float(fields[3])
                mv = float(fields[45])   # 总市值（亿元），A+H 股已含 H 股市值
            except Exception:
                continue
            if mv > 500.0 and price > 0.0:
                out.append((key, fields[1].strip(), mv, price))
    return out


def _iso_date(s):
    s = str(s or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def akshare_dividends(codes):
    """通过 akshare 巨潮接口逐只取历史分红，返回 {code: [record,...]}。"""
    result = {}
    for i, code in enumerate(codes):
        numeric = code[-6:] if len(code) >= 6 else code
        try:
            df = ak.stock_dividend_cninfo(symbol=numeric)
        except Exception:
            result[code] = []
            continue
        rows = []
        for _, row in df.iterrows():
            try:
                per10 = float(row.get("派息比例") or 0)
            except Exception:
                per10 = 0.0
            if per10 <= 0:
                continue
            ex_date = _iso_date(row.get("除权日"))
            report = str(row.get("报告时间") or "").strip()
            fy_match = re.search(r"(\d{4})", report)
            fy = f"{fy_match.group(1)}1231" if fy_match else ""
            rows.append({"exDiviDate": ex_date, "reportEndDate": fy, "cashDiviRMB": round(per10, 4)})
        result[code] = rows
        if (i + 1) % 100 == 0:
            print(f"     [div] 已处理 {i + 1}/{len(codes)}")
        time.sleep(0.05)
    return result


# ------------------------- A股构建 -------------------------
def _div_to_rows(divs):
    """A股分红记录 -> finalize_one 行格式。以 reportEndDate(财年) 归并；
    cashDiviRMB 已是『元/10股』，故 per10 直接取该值。"""
    rows = []
    for d in (divs or []):
        ex = d.get("exDiviDate", "")
        if isinstance(ex, (int, float)):
            ex = str(int(ex))
        ex = _iso_date(ex)
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


def build():
    print("[A股] 市值筛选股票池(腾讯 gtimg, 总市值>500亿元) ...")
    if USE_BUILTIN:
        print("  使用 WorkBuddy 内置 WeStock CLI")
        pool = westock_pool()
        dividend_fn = westock_dividends
    else:
        print("  使用 fallback: akshare + 腾讯 gtimg + 巨潮 cninfo")
        pool = akshare_pool()
        dividend_fn = akshare_dividends
    codes = [c for c, _, _, _ in pool]
    name_map = {c: n for c, n, _, _ in pool}
    mv_map = {c: mv for c, _, mv, _ in pool}      # TotalMV (亿元)
    price_map = {c: p for c, _, _, p in pool}      # ClosePrice (现价)
    print(f"     A股候选(>500亿元): {len(codes)}")
    divs_map = dividend_fn(codes)
    raw, done, skipped = [], 0, 0
    for code in codes:
        price = price_map.get(code, 0)
        mv_yi = mv_map.get(code, 0)
        if price <= 0 or mv_yi <= 0:
            print(f"     [skip] {code} {name_map.get(code,'')} — price={price} mv={mv_yi}")
            skipped += 1
            continue
        rows = _div_to_rows(divs_map.get(code, []))
        rec = finalize_one(code, name_map.get(code, code), price, mv_yi, rows)
        raw.append(rec)
        done += 1
        if done % 30 == 0:
            print(f"     [A] 已处理 {done}/{len(codes)}")
    print(f"     [A] 完成: {done} 只入库, {skipped} 只跳过")
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
        label = TIER_LABELS.get(key, f"tier_{key}")
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
