#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股 市值>1000亿 公司 股息率排名 —— 数据抓取与计算
- 市值来源: 腾讯 gtimg 实时行情 (总市值, 单位 亿元, 字段索引44)
- 分红来源: akshare stock_dividend_cninfo (巨潮历史分红, 含年度/中期/特别, 派息比例=元/10股)
- TTM 股息率 = 近12个月(除权日落入窗口)现金分红合计数 / 当前总市值
- LFY 股息率 = 最近一个完整财年(按报告时间取最大年)现金分红合计数 / 当前总市值
输出: data.json (供前端网页使用)；同时缓存分红到 dividends_cache.json
"""
import json
import re
import time
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import akshare as ak

WORKDIR = "/Users/green/WorkBuddy/2026-07-11-16-35-47"
OUT = f"{WORKDIR}/data.json"
CACHE = f"{WORKDIR}/dividends_cache.json"
TODAY = dt.date.today()  # 动态取运行当日，保证自动化每次刷新都用真实日期
TTM_START = TODAY - dt.timedelta(days=365)
MARKET_CAP_MIN = 1000.0  # 亿元

# ------------------------- 1. A股代码列表 -------------------------
def get_all_a_codes():
    df = ak.stock_info_a_code_name()
    out = []
    for _, r in df.iterrows():
        code = str(r["code"]).zfill(6)
        name = str(r["name"]).strip()
        out.append((code, name))
    return out


# ------------------------- 2. 腾讯行情市值 -------------------------
def tencent_prefix(code):
    if code.startswith(("60", "68", "9", "5", "11", "110", "113")):
        return "sh"
    if code.startswith(("8", "4")):
        return "bj"
    return "sz"


def fetch_market_caps(code_list):
    result = {}
    batch = 60
    for i in range(0, len(code_list), batch):
        chunk = code_list[i:i + batch]
        q = [f"{tencent_prefix(c)}{c}" for c, _ in chunk]
        url = "https://qt.gtimg.cn/q=" + ",".join(q)
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            text = r.content.decode("gbk", errors="ignore")
            for line in text.strip().split(";"):
                line = line.strip()
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                val = val.strip().strip('"')
                if not val:
                    continue
                parts = val.split("~")
                if len(parts) < 47:
                    continue
                code = parts[2]
                name = re.sub(r"^(XD|XR|DR|N)", "", parts[1]).replace(" ", "")
                try:
                    total_mv = float(parts[45])   # 总市值 亿元 (含H股)
                    price = float(parts[3])        # 当前价
                except (ValueError, IndexError):
                    continue
                result[code] = (name, total_mv, price)
        except Exception as e:
            print("  [mv] batch error:", e)
        time.sleep(0.25)
    return result


# ------------------------- 3. 分红历史(缓存) -------------------------
def load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache):
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def parse_div_rows(raw):
    """raw: akshare DataFrame -> list of {ex_date, per10, fy}"""
    rows = []
    for _, r in raw.iterrows():
        try:
            per10 = r.get("派息比例")
            if per10 in (None, "", "None") or (isinstance(per10, float) and per10 != per10):
                continue
            per10 = float(per10)
            if per10 <= 0:
                continue
            exd = r.get("除权日")
            ex_date = None
            if exd is not None and str(exd) not in ("NaT", "None", ""):
                ex_date = dt.datetime.strptime(str(exd), "%Y-%m-%d").date()
            if ex_date is None:
                continue
            # 财年: 取自报告时间(如 "2025年报"/"2025三季报"); 缺失则用除权日年份
            fy = None
            rt = str(r.get("报告时间", "") or "")
            m = re.search(r"(\d{4})", rt)
            if m:
                fy = int(m.group(1))
            else:
                fy = ex_date.year
            rows.append({"ex_date": ex_date.isoformat(), "per10": per10, "fy": fy,
                         "type": str(r.get("分红类型", "")), "desc": str(r.get("实施方案分红说明", ""))})
        except Exception:
            continue
    return rows


def get_dividends(code, cache):
    if code in cache:
        return cache[code]
    try:
        df = ak.stock_dividend_cninfo(symbol=code)
        rows = parse_div_rows(df)
    except Exception:
        rows = []
    cache[code] = rows
    return rows


# ------------------------- 4. 主流程 -------------------------
def main():
    print("[1/4] 获取A股代码列表 ...")
    codes = get_all_a_codes()
    print(f"      A股数: {len(codes)}")

    print("[2/4] 拉取腾讯市值并筛选 >1000亿 ...")
    mv_map = fetch_market_caps(codes)
    big = [(c, mv_map[c]) for c, n in codes if c in mv_map and mv_map[c][1] > MARKET_CAP_MIN]
    big.sort(key=lambda x: x[1][1], reverse=True)
    print(f"      市值>1000亿 公司数: {len(big)}")

    print("[3/4] 抓取分红历史(巨潮, 顺序执行+缓存) ...")
    cache = load_cache()
    need = [c for c, _ in big if c not in cache]
    print(f"      需新抓取: {len(need)} 只")

    # 注意: stock_dividend_cninfo 依赖 py_mini_racer(V8), 非线程安全, 必须顺序执行于主线程
    done = 0
    for c, _ in big:
        get_dividends(c, cache)
        done += 1
        if done % 20 == 0 or done == len(big):
            save_cache(cache)
            print(f"      已抓取 {done}/{len(big)}")
    save_cache(cache)

    print("[4/4] 计算 TTM / LFY 股息率并排名 ...")
    def fy_sum(rows, y):
        # 同一财年内可能多次分红(年度/中期/特别)，全部相加
        return sum(x["per10"] for x in rows if x["fy"] == y) if y != "" else 0.0
    records = []
    for code, (name, mv, price) in big:
        rows = cache.get(code, [])
        # TTM: 除权日 ∈ [TTM_START, TODAY]
        ttm_per10 = sum(x["per10"] for x in rows
                        if TTM_START <= dt.date.fromisoformat(x["ex_date"]) <= TODAY)
        # LFY: 最近财年(最大 fy) 全部分红(一年内多次分红已相加)
        fy_years = sorted({x["fy"] for x in rows}, reverse=True)
        lfy_year = fy_years[0] if fy_years else ""
        lfy_per10 = fy_sum(rows, lfy_year)
        # 前年 / 大前年(相对 LFY 财年, 计算口径同 LFY)
        prev_year = (lfy_year - 1) if isinstance(lfy_year, int) else ""
        prev2_year = (lfy_year - 2) if isinstance(lfy_year, int) else ""
        prev_per10 = fy_sum(rows, prev_year)
        prev2_per10 = fy_sum(rows, prev2_year)

        ttm_yield = (ttm_per10 / 10.0 / price * 100.0) if price > 0 else 0.0
        lfy_yield = (lfy_per10 / 10.0 / price * 100.0) if price > 0 else 0.0
        prev_yield = (prev_per10 / 10.0 / price * 100.0) if price > 0 else 0.0
        prev2_yield = (prev2_per10 / 10.0 / price * 100.0) if price > 0 else 0.0
        records.append({
            "code": code, "name": name,
            "price": round(price, 2), "total_mv_yi": round(mv, 2),
            "ttm_per10": round(ttm_per10, 4), "ttm_yield": round(ttm_yield, 3),
            "lfy_year": lfy_year, "lfy_per10": round(lfy_per10, 4), "lfy_yield": round(lfy_yield, 3),
            "prev_year": prev_year, "prev_per10": round(prev_per10, 4), "prev_yield": round(prev_yield, 3),
            "prev2_year": prev2_year, "prev2_per10": round(prev2_per10, 4), "prev2_yield": round(prev2_yield, 3),
        })

    ttm_rank = sorted([r for r in records if r["ttm_yield"] > 0], key=lambda x: x["ttm_yield"], reverse=True)[:30]
    lfy_rank = sorted([r for r in records if r["lfy_yield"] > 0], key=lambda x: x["lfy_yield"], reverse=True)[:30]

    out = {
        "generated_at": TODAY.isoformat(),
        "market_cap_min_yi": MARKET_CAP_MIN,
        "big_cap_count": len(big),
        "ttm_start": TTM_START.isoformat(),
        "ttm_rank": ttm_rank, "lfy_rank": lfy_rank, "all": records,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"      写入 {OUT}")
    print("      TTM 前5:", [(r["name"], r["ttm_yield"]) for r in ttm_rank[:5]])
    print("      LFY 前5:", [(r["name"], r["lfy_yield"], r["lfy_year"]) for r in lfy_rank[:5]])


if __name__ == "__main__":
    main()
