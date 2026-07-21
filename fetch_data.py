#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股 股息率排名（按市值分档）—— 数据抓取与计算
- 市值分档: 仅取 市值>500亿 的股票，再分为两档：>1000亿、500-1000亿（各档内取股息率 Top30）
- 市值来源: 腾讯 gtimg 实时行情 (总市值, 单位 亿元, 字段索引44)
- 分红来源: akshare stock_dividend_cninfo (巨潮历史分红, 含年度/中期/特别, 派息比例=元/10股)
- TTM 股息率 = 近12个月(实施方案公告日落入窗口)现金分红合计数 / 当前总市值
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
NAMES_CACHE = f"{WORKDIR}/names_cache.json"
TODAY = dt.date.today()  # 动态取运行当日，保证自动化每次刷新都用真实日期
TTM_START = TODAY - dt.timedelta(days=365)
# 市值分档：仅抓取并展示这两档，避免全市场数千只小市值股的超长抓取
FETCH_FLOOR = 500.0  # 亿元：只抓取 市值>500亿 的股票（覆盖下面两档）
TIERS = [
    ("gt1000", "市值 > 1000亿", lambda mv: mv > 1000.0),
    ("mid500", "1000亿 ≥ 市值 > 500亿", lambda mv: 500.0 < mv <= 1000.0),
]

# ------------------------- 1. A股代码列表 -------------------------
def _clean_name(s):
    """清洗证券简称：去对齐空格、全角字母数字转半角、去除 XD/XR/DR/N 交易状态前缀。"""
    s = str(s or "").strip().replace(" ", "").replace("\u3000", "")
    # 全角 ASCII(！-～) 转半角，如 万科Ａ -> 万科A
    s = "".join(chr(ord(c) - 0xFEE0) if "\uFF01" <= c <= "\uFF5E" else c for c in s)
    s = re.sub(r"^(XD|XR|DR|N)", "", s)
    return s


def get_all_a_codes():
    """直接用交易所静态名录(沪深)构建 代码->完整名称。
    关键：上交所改用「证券全称」字段——它在除权日也不会出现 XD 前缀定宽截断
    （公司简称 在除权日会变成 XD中国建，证券全称 始终是干净的 中国建筑）；
    深交所 A股简称 用空格对齐(非截断)，经 _clean_name 归一即可。
    跳过北交所(无 >1000亿, 且其接口不稳定)。"""
    out, seen = [], set()
    # 上交所：主板A股 + 科创板；优先 证券全称(不受 XD 定宽截断)，回退 公司简称/证券简称
    for board in ("主板A股", "科创板"):
        sh = ak.stock_info_sh_name_code(symbol=board)
        for _, r in sh.iterrows():
            code = str(r["证券代码"]).zfill(6)
            name = _clean_name(r.get("证券全称") or r.get("公司简称") or r.get("证券简称"))
            if code and code not in seen and name:
                out.append((code, name)); seen.add(code)
    # 深交所：A股简称(含对齐空格/全角字母，未截断) 经 _clean_name 归一
    sz = ak.stock_info_sz_name_code()
    for _, r in sz.iterrows():
        code = str(r["A股代码"]).strip()
        if not code or code in ("nan", "None"):
            continue
        code = code.zfill(6)
        name = _clean_name(r.get("A股简称"))
        if code and code not in seen and name:
            out.append((code, name)); seen.add(code)
    return out


# ------------------------- 1b. 名称缓存(跨运行持久化，自愈) -------------------------
def load_names_cache():
    try:
        with open(NAMES_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_names_cache(cache):
    with open(NAMES_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def reconcile_names(live, cache):
    """返回 code->最佳名称：取「本次实时名」与「历史缓存名」中去 XD 后较长者。
    自愈原理：非除权日名录返回完整名(如 中国建筑)，除权日返回截断名(中国建)，
    缓存永久保留更长者，杜绝任何单一来源的偶发截断。"""
    out = {}
    for code, nm in live.items():
        cur = _clean_name(nm)
        prev = _clean_name(cache.get(code, ""))
        best = cur if len(cur) >= len(prev) else prev
        if best:
            out[code] = best
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


def _parse_ratio(v):
    """把 派息比例/送股比例/转增比例 解析为 float；缺失/NaN/空 返回 0.0。"""
    if v is None or str(v) in ("None", "nan", "NaN", ""):
        return 0.0
    try:
        f = float(v)
        return f if f == f else 0.0   # NaN -> 0.0
    except (ValueError, TypeError):
        return 0.0


def parse_div_rows(raw):
    """raw: akshare DataFrame -> list of {ex_date, per10, fy, tag}
    tag(除权标记): 含现金派息=XD, 含送股/转增=XR, 两者皆有=DR, 无=空串。
    保留「现金派息 或 送转」任一项>0 的记录(纯 0 丢弃)，以便精确判断除权类型。"""
    rows = []
    for _, r in raw.iterrows():
        try:
            per10 = _parse_ratio(r.get("派息比例"))
            sg = _parse_ratio(r.get("送股比例"))
            zz = _parse_ratio(r.get("转增比例"))
            has_share = (sg > 0) or (zz > 0)
            # 仅保留确有分红/送转的记录
            if per10 <= 0 and not has_share:
                continue
            # 除权标记
            if per10 > 0 and has_share:
                tag = "DR"
            elif per10 > 0:
                tag = "XD"
            elif has_share:
                tag = "XR"
            else:
                tag = ""
            exd = r.get("除权日")
            ex_date = None
            if exd is not None and str(exd) not in ("NaT", "None", ""):
                ex_date = dt.datetime.strptime(str(exd), "%Y-%m-%d").date()
            # 公告日(实施方案公告日期)：TTM 窗口判定以此为准
            ann = r.get("实施方案公告日期")
            announce_date = None
            if ann is not None and str(ann) not in ("NaT", "None", ""):
                announce_date = dt.datetime.strptime(str(ann), "%Y-%m-%d").date()
            # 公告日缺失时回退除权日，避免漏算
            if announce_date is None:
                announce_date = ex_date
            if announce_date is None:
                continue
            # 财年: 取自报告时间(如 "2025年报"/"2025三季报"); 缺失则用公告日年份
            fy = None
            rt = str(r.get("报告时间", "") or "")
            m = re.search(r"(\d{4})", rt)
            if m:
                fy = int(m.group(1))
            else:
                fy = announce_date.year
            rows.append({"ex_date": ex_date.isoformat() if ex_date else "",
                         "announce_date": announce_date.isoformat(),
                         "per10": per10, "fy": fy, "tag": tag,
                         "type": str(r.get("分红类型", "")), "desc": str(r.get("实施方案分红说明", ""))})
        except Exception:
            continue
    return rows


def get_dividends(code, cache):
    """每次都重新抓取最新分红；仅当抓取异常时回退到已有缓存(保证当天榜单不空)。
    抓取成功但返回空、且历史有数据时，疑似瞬时空响应，保留旧缓存。"""
    try:
        df = ak.stock_dividend_cninfo(symbol=code)
        rows = parse_div_rows(df)
    except Exception:
        return cache.get(code, [])
    if not rows and code in cache and cache[code]:
        return cache[code]
    cache[code] = rows
    return rows


# ------------------------- 4. 主流程 -------------------------
def main():
    print("[1/4] 获取A股代码列表 ...")
    codes = get_all_a_codes()
    # 代码->完整名称映射：本次实时名 与 历史缓存名 取较长者(自愈，杜绝除权日 XD 截断)
    live_map = {c: n for c, n in codes}
    names_cache = load_names_cache()
    name_map = reconcile_names(live_map, names_cache)
    names_cache.update(name_map)
    save_names_cache(names_cache)
    print(f"      A股数: {len(codes)} | 名称缓存: {len(name_map)} 条")

    print("[2/4] 拉取腾讯市值并筛选 >500亿(覆盖两档) ...")
    mv_map = fetch_market_caps(codes)
    elig = [(c, mv_map[c]) for c, n in codes if c in mv_map and mv_map[c][1] > FETCH_FLOOR]
    elig.sort(key=lambda x: x[1][1], reverse=True)
    print(f"      市值>500亿 公司数: {len(elig)} (含 >1000亿 与 500-1000亿 两档)")

    print("[3/4] 全量刷新分红历史(巨潮, 顺序执行；缓存仅作失败兜底) ...")
    cache = load_cache()
    print(f"      待刷新: {len(elig)} 只（含两档）")

    # 注意: stock_dividend_cninfo 依赖 py_mini_racer(V8), 非线程安全, 必须顺序执行于主线程
    done = 0
    for c, _ in elig:
        get_dividends(c, cache)
        done += 1
        if done % 20 == 0 or done == len(elig):
            save_cache(cache)
            print(f"      已抓取 {done}/{len(elig)}")
    save_cache(cache)

    print("[4/4] 计算 TTM / LFY 股息率并按市值分档排名 ...")
    def fy_sum(rows, y):
        # 同一财年内可能多次分红(年度/中期/特别)，全部相加
        return sum(x["per10"] for x in rows if x["fy"] == y) if y != "" else 0.0
    records = []
    for code, (name, mv, price) in elig:
        # 优先使用 akshare 完整名称，回退腾讯简称
        name = name_map.get(code, name)
        rows = cache.get(code, [])
        # 除权标记：今日若为其除权日，取该次分红的 XD/XR/DR 标记(空串=非除权日)；过后自动恢复正常名
        ex_rows = [x for x in rows if str(x.get("ex_date", "")) == TODAY.isoformat()]
        ex_tag = ex_rows[0].get("tag", "") if ex_rows else ""
        # TTM: 公告日(实施方案公告日期) ∈ [TTM_START, TODAY]
        ttm_rows = [x for x in rows
                    if TTM_START <= dt.date.fromisoformat(x["announce_date"]) <= TODAY]
        ttm_per10 = sum(x["per10"] for x in ttm_rows)
        ttm_div_count = sum(1 for x in ttm_rows if x["per10"] > 0)   # TTM 窗口内现金分红次数(多次分别计数)
        # LFY: 最近财年(最大 fy) 全部分红(一年内多次分红已相加)
        fy_years = sorted({x["fy"] for x in rows}, reverse=True)
        lfy_year = fy_years[0] if fy_years else ""
        lfy_per10 = fy_sum(rows, lfy_year)
        lfy_div_count = (sum(1 for x in rows if x["fy"] == lfy_year and x["per10"] > 0)
                         if isinstance(lfy_year, int) else 0)  # 该财年现金分红次数
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
            "code": code, "name": name, "ex_tag": ex_tag,
            "price": round(price, 2), "total_mv_yi": round(mv, 2),
            "ttm_per10": round(ttm_per10, 4), "ttm_yield": round(ttm_yield, 3),
            "ttm_div_count": ttm_div_count,
            "lfy_year": lfy_year, "lfy_per10": round(lfy_per10, 4), "lfy_yield": round(lfy_yield, 3),
            "lfy_div_count": lfy_div_count,
            "prev_year": prev_year, "prev_per10": round(prev_per10, 4), "prev_yield": round(prev_yield, 3),
            "prev2_year": prev2_year, "prev2_per10": round(prev2_per10, 4), "prev2_yield": round(prev2_yield, 3),
        })

    tiers_out = []
    for key, label, pred in TIERS:
        sub = [r for r in records if pred(r["total_mv_yi"])]
        ttm_rank = sorted([r for r in sub if r["ttm_yield"] > 0], key=lambda x: x["ttm_yield"], reverse=True)[:30]
        lfy_rank = sorted([r for r in sub if r["lfy_yield"] > 0], key=lambda x: x["lfy_yield"], reverse=True)[:30]
        tiers_out.append({"key": key, "label": label, "count": len(sub),
                          "ttm_rank": ttm_rank, "lfy_rank": lfy_rank})
        print(f"      档[{label}] 公司数={len(sub)} TTM前3={[(r['name'], r['ttm_yield']) for r in ttm_rank[:3]]}")

    out = {
        "generated_at": TODAY.isoformat(),
        "ttm_start": TTM_START.isoformat(),
        "tiers": tiers_out,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"      写入 {OUT}")


if __name__ == "__main__":
    main()
