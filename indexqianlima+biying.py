# -*- coding: utf-8 -*-
"""
全国范围 燃料电池 + 甲醇制氢招标采集 + 钉钉机器人分批推送
管道：全国聚合页线索 → 必应 site: 溯源 → 直连解析 → 种子兜底 → 钉钉分批推送
"""
import os, re, time, hmac, hashlib, base64, requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright





# ── 基础配置 ──────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

KEYWORDS = ["燃料电池", "氢燃料电池", "甲醇制氢", "掺氢"]
LOOKBACK_DAYS = 3
STRONG_SIGNALS = ["招标公告", "谈判采购公告", "竞争性谈判", "询比采购", "项目编号",
                 "采购人", "招标人", "报价截止", "投标截止", "招标编号", "询价公告"]



BATCH_SIZE = 3  # 每批推送条数，确保不超钉钉字数限制

# ── 数据源配置 ────────────────────────────────────────────
AGGREGATE_SEEDS = [
    "https://wap.qianlima.com/gjxx/79617/index_12.html",
    "https://wap.qianlima.com/gjxx/481701/index_29_0.html",
    "https://wap.qianlima.com/gjxx/83918/index_23_0.html",
    "https://www.dljczb.com/rd-106990_dzcg",
    "https://www.dlnyzb.com/detail/41952221",
]

ORIGIN_DOMAINS = [
    "ggzy.gov.cn", "ggzyjy.dl.gov.cn",
    "zhongheneng.ccpc360.com", "ec.ceec.net.cn",
    "chinabidding.cn", "ccpc360.com", "dlnyzb.com",
    "m.weichai.com", "wbpm.weichai.com",
    "sizebid.com", "ebnew.com", "ne21.com", "dljczb.com",
]

DETAIL_SEEDS = [
    ("https://ggzyjy.dl.gov.cn/jyxx/002006/002006001/002006001003/20260814/ba615db6-31e1-479f-8444-0d2cb56bde4d.html", "辽宁省", "大连市公共资源交易系统"),
    ("https://m.weichai.com/group/weichai/cn/media-hub/tendering/2026081115304222909/index.html", "山东省", "潍柴电子招标系统"),
    ("https://m.weichai.com/group/weichai/cn/media-hub/tendering/2026081115421278393/index.html", "山东省", "潍柴电子招标系统"),
    ("https://www.dlnyzb.com/detail/41952221", "辽宁省", "华电科工采购平台"),
    ("https://zhongheneng.ccpc360.com/", "广东省", "中核汇能电子采购平台"),
    ("https://www.dljczb.com/rd-106990_dzcg", "全国", "电力集采招标网"),
    ("https://ec.ceec.net.cn/HomeInfo/ProjectDetail.aspx?bigtype=QwBHAEcARwA%3D&threadID=ec1f0d17-d61b-420b-91fb-6e464925db03", "辽宁省", "中国能建电子采购平台"),
]

# ── 工具函数 ──────────────────────────────────────────────
def detect_platform(url):
    if "ggzyjy.dl.gov.cn" in url: return "大连市公共资源交易系统"
    if "ggzy.gov.cn" in url: return "全国公共资源交易平台"
    if "zhongheneng.ccpc360.com" in url: return "中核汇能电子采购平台"
    if "ec.ceec.net.cn" in url: return "中国能建电子采购平台"
    if "chinabidding.cn" in url: return "采购与招标网"
    if "ccpc360.com" in url: return "招标采购导航网"
    if "dlnyzb.com" in url: return "电力能源招标网"
    if "m.weichai.com" in url or "wbpm.weichai.com" in url: return "潍柴电子招标系统"
    if "sizebid.com" in url: return "乙方宝/尺寸招标"
    if "dljczb.com" in url: return "电力集采招标网"
    return "其他来源"

# ── 采集层 ────────────────────────────────────────────────
def fetch_aggregate_clues():
    clues = []
    cutoff = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    print("🎯 第一层：全国聚合页线索提取")
    for page_url in AGGREGATE_SEEDS:
        try:
            print(f"  🔎 {page_url}")
            resp = requests.get(page_url, headers=HEADERS, timeout=15)
            resp.encoding = "utf-8"
            if resp.status_code != 200: continue
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text("\n")
            for line in text.split("\n"):
                line = line.strip()
                if len(line) < 8: continue
                if not any(kw in line for kw in KEYWORDS): continue
                if not any(sig in line for sig in STRONG_SIGNALS): continue
                pub_date = ""
                m = re.search(r"(\d{4})-(\d{2})-(\d{2})", line)
                if m: pub_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                else:
                    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", line)
                    if m: pub_date = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
                if not pub_date: continue
                try:
                    pd = datetime.strptime(pub_date, "%Y-%m-%d")
                    if pd < cutoff or pd > datetime.now(): continue
                except: continue
                title = line
                for kw in KEYWORDS:
                    idx = title.find(kw)
                    if idx >= 0:
                        s, e = max(0, idx-30), min(len(line), idx+len(kw)+40)
                        candidate = line[s:e].strip()
                        if len(candidate) > 10: title = candidate; break
                detail_url = ""
                for a in soup.find_all("a", href=True):
                    if any(kw in a.get_text(strip=True) for kw in KEYWORDS):
                        detail_url = a["href"]
                        if not detail_url.startswith("http"):
                            detail_url = urljoin(page_url, detail_url)
                        break
                clue = {"title": title, "pub_date": pub_date, "aggregate_url": page_url,
                        "detail_url": detail_url, "province": "全国", "source": detect_platform(page_url)}
                if title not in [c["title"] for c in clues]:
                    clues.append(clue)
                    print(f"    🔗 {title[:55]} ({pub_date})")
        except Exception as e:
            print(f"    ⚠️ {e}")
        time.sleep(1)
    return clues

def trace_origin_via_bing(clue):
    print(f"\n  🔎 必应溯源: {clue['title'][:35]}")
    search_terms = []
    buyers = re.findall(r"(.+?)(?:有限公司|股份公司|研究院|公司|集团)", clue["title"])
    for b in buyers[:2]:
        b = b.strip()
        if len(b) >= 4: search_terms.append(b)
    proj_nos = re.findall(r"项目编号[:：]?\s*([A-Za-z0-9\-]+)", clue["title"])
    for p in proj_nos: search_terms.append(p)
    if not search_terms: search_terms = [clue["title"][:30]]
    for term in search_terms[:3]:
        query = f'"{term}" {"招标" if "招标" in clue["title"] else "采购"}'
        try:
            resp = requests.get(f"https://www.bing.com/search?q={quote_plus(query)}", headers=HEADERS, timeout=15)
            if resp.status_code != 200: continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for li in soup.select("li.b_algo"):
                a = li.select_one("h2 a")
                if not a: continue
                href, title = a.get("href", ""), a.get_text(strip=True)
                if not any(d in href for d in ORIGIN_DOMAINS): continue
                if not any(kw in title for kw in KEYWORDS): continue
                print(f"    ✅ 溯源: {href}")
                return {"origin_url": href, "origin_title": title, "origin_source": detect_platform(href)}
        except: pass
        time.sleep(1)
    return None

def parse_origin_detail(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = resp.apparent_encoding
        if resp.status_code != 200: return None
        soup = BeautifulSoup(resp.text, "html.parser")
        body = soup.get_text("\n", strip=True)
        if not any(sig in body for sig in STRONG_SIGNALS) and not any(kw in body for kw in KEYWORDS):
            return None
        title = soup.h1.get_text(strip=True) if soup.h1 else (soup.title.get_text(strip=True) if soup.title else "")
        if len(title) < 6:
            for line in body.split("\n"):
                if any(kw in line for kw in KEYWORDS):
                    title = line.strip(); break
        pub_date = ""
        m = re.search(r"发布时间[:：]?\s*(\d{4}-\d{2}-\d{2})", body) or re.search(r"(\d{4})-(\d{2})-(\d{2})", url)
        if m: pub_date = m.group(1)
        else:
            m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", body)
            if m: pub_date = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
        project_no = ""
        m = re.search(r"项目编号[:：]?\s*([A-Za-z0-9\-第号]+)", body)
        if m: project_no = m.group(1).strip()
        buyer = ""
        for pat in [r"招标人[:：]\s*([^\n]+)", r"采购人[:：]\s*([^\n]+)", r"询价方[:：]\s*([^\n]+)"]:
            m = re.search(pat, body)
            if m:
                buyer = re.sub(r"联系方式.*$", "", m.group(1).strip()).strip()[:50]
                break
        price = ""
        for pat in [r"预算金额[:：]?\s*([\d,]+\.?\d*)\s*(万元|元|亿元)",
                    r"最高限价[:：]?\s*([\d,]+\.?\d*)\s*(万元|元|亿元)",
                    r"中标金额[:：]?\s*([\d,]+\.?\d*)\s*(万元|元|亿元)"]:
            m = re.search(pat, body)
            if m:
                val = float(m.group(1).replace(",", ""))
                price = f"{val}亿元" if m.group(2) == "亿元" else (f"{val}万元" if m.group(2) == "万元" else f"{val/10000:.2f}万元")
                break
        deadline = ""
        for pat in [r"投标截止时间[:：]?\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[ 日号]*\s*\d{0,2}:?\d{0,2})",
                    r"截标/开标时间[:：]?\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}\s*\d{1,2}:\d{2})",
                    r"报价截止时间[:：]?\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}\s*\d{1,2}:\d{2})"]:
            m = re.search(pat, body)
            if m: deadline = m.group(1).strip(); break
        notice_type = "招标公告"
        if "中标" in title or "成交" in title: notice_type = "中标/成交公告"
        elif "竞争性谈判" in title: notice_type = "竞争性谈判"
        elif "询比" in title or "询价" in title: notice_type = "询比/询价公告"
        elif "谈判采购" in title: notice_type = "谈判采购"
        return {"title": title, "project_no": project_no, "buyer": buyer, "price": price,
                "deadline": deadline, "notice_type": notice_type, "publish_date": pub_date,
                "origin_url": url, "origin_source": detect_platform(url)}
    except Exception as e:
        print(f"    ❌ 解析失败 {url}: {e}")
        return None

def add_seed_details():
    print("\n🎯 兜底层：直连已验证种子详情页")
    items = []
    for url, province, platform in DETAIL_SEEDS:
        print(f"  🔗 {url}")
        item = parse_origin_detail(url)
        if item:
            item["province"] = province
            items.append(item)
            print(f"    ✅ {item['title'][:50]}")
        time.sleep(1)
    return items

# ── 钉钉推送（支持分批） ──────────────────────────────────
def get_sign():
    ts = str(round(time.time() * 1000))
    h = hmac.new(SECRET.encode(), f"{ts}\n{SECRET}".encode(), hashlib.sha256).digest()
    return ts, quote_plus(base64.b64encode(h))

def send_dingtalk(content, page_num=1, total_pages=1):
    if not WEBHOOK:
        print("  ⚠️ 未配置 DINGTALK_WEBHOOK，跳过推送")
        return {"errcode": -1, "errmsg": "no webhook"}
    ts, sign = get_sign()
    title = f"燃料电池+甲醇制氢招标日报（第{page_num}/{total_pages}批）" if total_pages > 1 else "燃料电池+甲醇制氢招标日报"
    try:
        r = requests.post(
            f"{WEBHOOK}&timestamp={ts}&sign={sign}",
            json={"msgtype": "markdown", "markdown": {"title": title, "text": content}},
            timeout=15
        )
        return r.json()
    except Exception as e:
        return {"errcode": -1, "errmsg": str(e)}

def generate_report(items, page_num=1, total_pages=1):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cutoff = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    page_info = f"（第 **{page_num}/{total_pages}** 批）" if total_pages > 1 else ""
    lines = [
        f"## 📋 燃料电池+甲醇制氢招标日报（全国范围）{page_info}\n",
        f"> 生成时间：{now} ｜ 本批 **{len(items)}** 条 ｜ 全量共 **{total_pages}** 批 ｜ 关键词：燃料电池/甲醇制氢/掺氢 ｜ 时间窗：近 **{LOOKBACK_DAYS}** 天（{cutoff.strftime('%Y-%m-%d')} ~ {datetime.now().strftime('%Y-%m-%d')}）\n\n",
    ]
    if not items:
        lines.append("> ⚠️ 本期未采集到有效公告\n")
    for i, it in enumerate(items, 1):
        global_idx = (page_num - 1) * BATCH_SIZE + i
        lines.append(f"\n**{global_idx}. {it['title']}**\n")
        lines.append(f"> 📍 省份：{it.get('province', '全国')} ｜ 🏛️ 来源：{it['origin_source']} ｜ 📋 类型：{it['notice_type']}\n")
        if it["project_no"]: lines.append(f"> 🔖 项目编号：{it['project_no']}\n")
        if it["buyer"]: lines.append(f"> 👤 采购人/招标人：{it['buyer']}\n")
        if it["price"]: lines.append(f"> 💰 预算/金额：**{it['price']}**\n")
        if it["deadline"]: lines.append(f"> ⏰ 截止时间：`{it['deadline']}`\n")
        if it["publish_date"]: lines.append(f"> 📅 发布日期：`{it['publish_date']}`\n")
        lines.append(f"> 🔗 [查看详情]({it['origin_url']})\n")
    lines.append("\n---\n采集架构：聚合页 → 必应溯源 → 直连解析 → 种子兜底")
    return "".join(lines)

# ── 主流程 ────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("🚀 全国范围 燃料电池+甲醇制氢招标采集 + 钉钉分批推送")
    print(f"   时间窗：近 {LOOKBACK_DAYS} 天 | 每批推送：{BATCH_SIZE} 条")
    print("=" * 70)

    all_items = []

    clues = fetch_aggregate_clues()
    print(f"\n📊 第一层得到 {len(clues)} 条线索")

    for clue in clues:
        print(f"\n📍 处理: {clue['title'][:50]}")
        origin = trace_origin_via_bing(clue)
        if origin:
            item = parse_origin_detail(origin["origin_url"])
            if item:
                item["province"] = clue.get("province", "全国")
                all_items.append(item)
                print(f"    ✅ {item['title'][:50]}")
        else:
            item = {"title": clue["title"], "project_no": "", "buyer": "", "price": "",
                    "deadline": "", "notice_type": "招标公告", "publish_date": clue["pub_date"],
                    "origin_url": clue["aggregate_url"], "origin_source": clue["source"],
                    "province": clue.get("province", "全国")}
            all_items.append(item)
            print(f"    ✅ [聚合页] {item['title'][:50]}")
        time.sleep(1)

    seed_items = add_seed_details()
    all_items.extend(seed_items)

    # 去重
    seen, unique_items = set(), []
    for it in all_items:
        key = (it["title"][:30], it["origin_url"])
        if key not in seen:
            seen.add(key)
            unique_items.append(it)

    total_items = len(unique_items)
    total_pages = max(1, (total_items + BATCH_SIZE - 1) // BATCH_SIZE)
    print(f"\n📄 去重后共 {total_items} 条公告，分 {total_pages} 批推送（每批 {BATCH_SIZE} 条）")

    if not WEBHOOK:
        print("⚠️ 未设置 DINGTALK_WEBHOOK 环境变量，仅本地输出报告：")
        for i in range(0, total_items, BATCH_SIZE):
            batch = unique_items[i:i+BATCH_SIZE]
            pn = i // BATCH_SIZE + 1
            print(f"\n{'='*50}\n📤 第 {pn}/{total_pages} 批\n{'='*50}")
            print(generate_report(batch, pn, total_pages))
        return

    # 逐批推送钉钉
    for i in range(0, total_items, BATCH_SIZE):
        batch = unique_items[i:i+BATCH_SIZE]
        page_num = i // BATCH_SIZE + 1
        report = generate_report(batch, page_num, total_pages)

        print(f"\n📤 推送第 {page_num}/{total_pages} 批（{len(batch)} 条）...")
        result = send_dingtalk(report, page_num, total_pages)
        print(f"📤 钉钉响应: {result}")

        # 批次间间隔，避免触发钉钉限流（官方建议 ≥ 1s）
        if page_num < total_pages:
            time.sleep(1.5)

    print(f"\n🏁 完成。共推送 {total_items} 条公告，分 {total_pages} 批。")

if __name__ == "__main__":
    main()
