#!/usr/bin/env python3
"""
品牌舆情监控系统 — Baseus倍思
多源全网监控 + 负面检测引擎 + 邮件汇总推送
运行在 GitHub Actions 上，全自动 7×24 小时
"""

import feedparser
import smtplib
import ssl
import os
import re
import time
import hashlib
import json
import urllib.request
import urllib.parse
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple
from collections import OrderedDict
import html as html_lib

# ============================================================
# 环境变量配置（GitHub Secrets 传入）
# ============================================================

EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "")

# 可选：Bing Web Search API Key（免费 1000次/月）
# 注册地址：https://portal.azure.com → 创建 Bing Search v7 资源
BING_API_KEY = os.environ.get("BING_API_KEY", "")

# ============================================================
# 品牌配置
# ============================================================

BRAND_NAME = "Baseus"
BRAND_NAME_CN = "倍思"
BRAND_NAMES = [BRAND_NAME, BRAND_NAME_CN, "baseus", "BASEUS", "Beseus"]  # 含常见拼错

# 品牌相关产品关键词（用于精确匹配）
BRAND_PRODUCTS = [
    "充电器", "充电头", "充电宝", "数据线", "耳机", "蓝牙耳机",
    "无线充", "车充", "车载充电", "移动电源", "氮化镓", "GaN",
    "充电", "线材", "插头", "电源适配器", "充电座", "磁吸充电",
    "chager", "charger", "cable", "power bank", "earbuds", "earphone",
]

# ============================================================
# 负面关键词引擎（分级）
# ============================================================

# 高危关键词 → 立即标红告警
NEGATIVE_HIGH = [
    # 安全事故
    "爆炸", "起火", "着火", "燃烧", "自燃", "烧毁", "烧了",
    "爆炸了", "火灾", "冒烟", "浓烟", "电死人", "触死",
    "电击身亡", "烧伤", "烧坏",
    # 人身伤害
    "电伤", "漏电", "触电", "炸伤", "炸了",
    # 骗局/诈骗
    "诈骗", "骗局", "骗钱", "骗子", "假货", "假冒", "冒充",
    "山寨", "fake", "counterfeit", "fraud", "scam",
    # 产品召回/通报
    "召回", "recall", "下架", "禁售", "查封", "处罚通知",
    "市场监管局", "缺陷产品", "检测不合格",
    # 集体投诉
    "集体投诉", "群体投诉", "大量投诉", "集体维权",
]

# 中危关键词 → 标记关注
NEGATIVE_MEDIUM = [
    # 质量问题
    "质量问题", "品控差", "质量差", "质量不行", "质量太差",
    "缺陷", "通病", "批量问题", "设计缺陷", "产品缺陷",
    "坏了", "坏了又坏", "用不了几天",
    # 投诉相关
    "投诉", "12315", "消协", "工商", "维权", "曝光", "内幕",
    "黑幕", "举报", "差评", "投诉无门", "投诉举报",
    "complain", "complaint",
    # 售后问题
    "售后差", "售后垃圾", "不保修", "拒绝保修", "推脱",
    "客服态度", "客服差", "不理人", "踢皮球", "推诿",
    "售后无门", "无售后", "售后困难",
    # 虚假宣传
    "虚假宣传", "夸大宣传", "虚假广告", "虚假标注",
    "参数虚标", "虚标容量", "功率虚标", "假宣传",
    # 数据线/充电问题
    "充不进电", "充不了电", "不充电", "不能充电", "无法充电",
    "越充电越少", "充电很慢", "充电发热", "发烫", "烫手",
    "过热", "温度过高", "高温",
    # 产品损坏
    "烧手机", "烧了手机", "烧坏手机", "烧设备", "烧主板",
    "烧充电口", "损伤电池", "伤电池", "损坏设备",
]

# 低危关键词 → 一般关注
NEGATIVE_LOW = [
    "不好用", "难用", "不好", "不行", "失望", "后悔",
    "不值", "坑", "坑爹", "烂", "垃圾产品",
    "断连", "连不上", "不稳定", "经常断",
    "没用", "废了", "浪费钱", "智商税",
    "做工差", "做工不好", "品控问题", "缝隙大",
]

# 权重分值
SEVERITY_SCORE = {
    "high": 10,
    "medium": 5,
    "low": 2,
}


def detect_negative(text: str) -> Tuple[str, List[str], int]:
    """
    检测文本中的负面信息。
    返回: (严重级别, 命中关键词列表, 危险分值)
    """
    text_lower = text.lower()
    score = 0
    keywords = []

    # 高危检测
    high_hits = [kw for kw in NEGATIVE_HIGH if kw.lower() in text_lower]
    if high_hits:
        keywords.extend(high_hits)
        score += len(high_hits) * SEVERITY_SCORE["high"]

    # 中危检测
    medium_hits = [kw for kw in NEGATIVE_MEDIUM if kw.lower() in text_lower]
    if medium_hits:
        keywords.extend(medium_hits)
        score += len(medium_hits) * SEVERITY_SCORE["medium"]

    # 低危检测
    low_hits = [kw for kw in NEGATIVE_LOW if kw.lower() in text_lower]
    if low_hits:
        keywords.extend(low_hits)
        score += len(low_hits) * SEVERITY_SCORE["low"]

    # 判定级别
    if score >= 15:
        level = "🔴 高危"
    elif score >= 5:
        level = "🟡 中危"
    elif score > 0:
        level = "🟢 低危"
    else:
        level = ""

    # 如果命中高危关键词，直接升级为高危
    if high_hits:
        level = "🔴 高危"

    return level, keywords, score


def contains_brand(text: str) -> bool:
    """检查文本是否包含品牌名"""
    text_lower = text.lower()
    for name in BRAND_NAMES:
        if name.lower() in text_lower:
            return True
    return False


# ============================================================
# 数据源采集
# ============================================================

def strip_html(text: str) -> str:
    """去除 HTML 标签"""
    clean = re.sub(r"<[^>]+>", "", text)
    clean = html_lib.unescape(clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def strip_cdata(text: str) -> str:
    """去除 CDATA 包装"""
    return re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.DOTALL)


def fetch_url(url: str, timeout: int = 15) -> Optional[str]:
    """通用 URL 抓取，返回文本内容"""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            # 尝试解码
            for encoding in ["utf-8", "gb2312", "gbk", "gb18030", "latin-1"]:
                try:
                    return raw.decode(encoding)
                except (UnicodeDecodeError, LookupError):
                    continue
            return raw.decode("utf-8", errors="ignore")
    except Exception:
        return None


def search_bing_rss(query: str, count: int = 20) -> List[Dict]:
    """通过 Bing RSS 搜索新闻"""
    results = []
    encoded = urllib.parse.quote(query)
    url = f"https://www.bing.com/news/search?q={encoded}&format=rss&count={count}"

    text = fetch_url(url, timeout=15)
    if not text:
        return results

    text = strip_cdata(text)
    feed = feedparser.parse(text)

    if feed.bozo and not feed.entries:
        return results

    for entry in feed.entries[:count]:
        title = strip_html(entry.get("title", ""))
        link = entry.get("link", "")
        summary = strip_html(entry.get("summary", entry.get("description", "")))[:300]

        if not title:
            continue

        results.append({
            "title": title,
            "link": link,
            "summary": summary,
            "source": "Bing News",
            "type": "搜索引擎",
        })

    return results


def search_bing_api(query: str, count: int = 20) -> List[Dict]:
    """通过 Bing Web Search API 搜索（免费 1000次/月）"""
    if not BING_API_KEY:
        return []

    results = []
    try:
        url = f"https://api.bing.microsoft.com/v7.0/search?q={urllib.parse.quote(query)}&count={count}&mkt=zh-CN"
        req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": BING_API_KEY})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read())

        for item in data.get("webPages", {}).get("value", [])[:count]:
            results.append({
                "title": strip_html(item.get("name", "")),
                "link": item.get("url", ""),
                "summary": strip_html(item.get("snippet", ""))[:300],
                "source": "Bing API",
                "type": "搜索引擎",
            })

        # 也搜新闻
        url_news = f"https://api.bing.microsoft.com/v7.0/news/search?q={urllib.parse.quote(query)}&count={count}&mkt=zh-CN"
        req2 = urllib.request.Request(url_news, headers={"Ocp-Apim-Subscription-Key": BING_API_KEY})
        with urllib.request.urlopen(req2, timeout=15) as response:
            data2 = json.loads(response.read())

        for item in data2.get("value", [])[:count]:
            results.append({
                "title": strip_html(item.get("name", "")),
                "link": item.get("url", ""),
                "summary": strip_html(item.get("description", ""))[:300],
                "source": "Bing News API",
                "type": "新闻",
            })
    except Exception:
        pass

    return results


def search_google_news(query: str) -> List[Dict]:
    """通过 Google News RSS 搜索（GitHub Actions 海外 IP 可访问）"""
    results = []
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"

    text = fetch_url(url, timeout=15)
    if not text:
        return results

    text = strip_cdata(text)
    feed = feedparser.parse(text)

    if not feed.entries:
        return results

    for entry in feed.entries[:15]:
        title = strip_html(entry.get("title", "")).split(" - ")[0]  # Google News 标题含来源后缀
        link = entry.get("link", "")
        summary = strip_html(entry.get("summary", entry.get("description", "")))[:300]

        if not title:
            continue

        results.append({
            "title": title,
            "link": link,
            "summary": summary,
            "source": "Google News",
            "type": "新闻",
        })

    return results


def search_tousu_sina(query: str) -> List[Dict]:
    """抓取黑猫投诉搜索页"""
    results = []
    encoded = urllib.parse.quote(query)
    url = f"https://tousu.sina.com.cn/search?q={encoded}"

    text = fetch_url(url, timeout=15)
    if not text:
        return results

    # 解析搜索结果
    # 黑猫投诉的搜索结果在 HTML 中，用正则提取
    # 匹配投诉条目链接和标题
    pattern = r'<a[^>]*href="(/complaint/view/\d+/)[^"]*"[^>]*title="([^"]*)"[^>]*>([^<]*)</a>'
    matches = re.findall(pattern, text)

    for href, title, inner in matches[:15]:
        full_url = f"https://tousu.sina.com.cn{href}"
        display_title = strip_html(title or inner)
        if not display_title:
            continue

        results.append({
            "title": display_title,
            "link": full_url,
            "summary": "",
            "source": "黑猫投诉",
            "type": "投诉平台",
        })

    # 备用：匹配 JSON-LD 或内嵌数据
    if not results:
        # 更宽松的匹配
        alt_pattern = r'<a[^>]*href="(/complaint/view/\d+/)[^"]*"[^>]*>([^<]+)</a>'
        for href, inner in re.findall(alt_pattern, text)[:15]:
            full_url = f"https://tousu.sina.com.cn{href}"
            display_title = strip_html(inner)
            if display_title and len(display_title) > 5:
                results.append({
                    "title": display_title,
                    "link": full_url,
                    "summary": "",
                    "source": "黑猫投诉",
                    "type": "投诉平台",
                })

    return results


def search_12315(query: str) -> List[Dict]:
    """抓取 12315 消费者投诉平台搜索结果"""
    results = []
    # 尝试多个 12315 相关的搜索源
    sources = [
        f"https://www.12315.cn/search?q={urllib.parse.quote(query)}",
        f"https://www.bing.com/search?q=site:12315.cn+{urllib.parse.quote(query)}&format=rss",
    ]

    for url in sources[:1]:  # 只试主站
        text = fetch_url(url, timeout=15)
        if not text:
            continue

        # 简单匹配标题模式
        for match in re.findall(r'<a[^>]*>([^<]*{}[^<]*)</a>'.format(re.escape(query)), text, re.IGNORECASE)[:10]:
            title = strip_html(match)
            if title and len(title) > 5:
                results.append({
                    "title": title,
                    "link": "",
                    "summary": "",
                    "source": "12315投诉",
                    "type": "投诉平台",
                })

    return results


def search_zhihu(query: str) -> List[Dict]:
    """搜索知乎相关讨论"""
    results = []
    encoded = urllib.parse.quote(query)
    url = f"https://www.zhihu.com/rss/search?q={encoded}"

    text = fetch_url(url, timeout=15)
    if not text:
        # zhihu RSS 可能需 cookie，用 Bing 间接搜
        bing_query = f'site:zhihu.com {query}'
        return search_bing_rss(bing_query, count=10)

    text = strip_cdata(text)
    feed = feedparser.parse(text)
    for entry in feed.entries[:10]:
        results.append({
            "title": strip_html(entry.get("title", "")),
            "link": entry.get("link", ""),
            "summary": strip_html(entry.get("summary", ""))[:200],
            "source": "知乎",
            "type": "社交平台",
        })

    return results


def search_weibo(query: str) -> List[Dict]:
    """间接搜索微博（通过搜索引擎）"""
    bing_query = f'site:weibo.com {query}'
    return search_bing_rss(bing_query, count=10)


# ============================================================
# 搜索引擎组装
# ============================================================

# 搜索查询模板：品牌名 + 负面关键词组合
SEARCH_QUERIES = [
    # 品牌名本身
    "{brand}",
    # 投诉相关
    "{brand} 投诉",
    "{brand} 质量问题",
    "{brand} 售后",
    "{brand} 曝光",
    "{brand} 维权",
    "{brand_cn} 投诉",
    "{brand_cn} 质量问题",
    "{brand_cn} 曝光",
    "{brand_cn} 质检 不合格",
    # 安全事故
    "{brand} 爆炸",
    "{brand_cn} 起火",
    "{brand} 召回",
    # 评测/讨论
    "{brand} 评测 差",
    "{brand_cn} 翻车",
    "{brand} 不推荐",
    # 平台投诉
    "{brand_cn} 黑猫",
    "{brand} 12315",
    # 英文
    "{brand} complaint",
    "{brand} problem",
    "{brand} review negative",
]


def collect_all_results() -> List[Dict]:
    """收集所有来源的搜索结果"""
    all_results = []
    seen_titles = set()

    def add_unique(items: List[Dict]):
        for item in items:
            key = hashlib.md5(item["title"].encode()).hexdigest()
            if key not in seen_titles:
                seen_titles.add(key)
                all_results.append(item)

    print("🔍 开始多源品牌舆情监控...\n")

    for query_template in SEARCH_QUERIES:
        query = query_template.format(brand=BRAND_NAME, brand_cn=BRAND_NAME_CN)

        # Bing RSS (免费，无需 API key)
        bing_results = search_bing_rss(query, count=10)
        add_unique(bing_results)
        print(f"  Bing RSS [{query}]: {len(bing_results)} 条")

        # Bing API (如果配置了 API key)
        if BING_API_KEY:
            api_results = search_bing_api(query, count=10)
            add_unique(api_results)
            print(f"  Bing API [{query}]: {len(api_results)} 条")

        # Google News (海外 IP 直连)
        google_results = search_google_news(query)
        add_unique(google_results)
        print(f"  Google News [{query}]: {len(google_results)} 条")

        # 避免频繁请求
        time.sleep(0.5)

    # 黑猫投诉（不需要 API key）
    print("\n📋 投诉平台...")
    tousu_results = search_tousu_sina(BRAND_NAME_CN)
    add_unique(tousu_results)
    print(f"  黑猫投诉: {len(tousu_results)} 条")

    tousu2 = search_tousu_sina(BRAND_NAME)
    add_unique(tousu2)
    print(f"  黑猫投诉(EN): {len(tousu2)} 条")

    # 12315
    cn12315 = search_12315(f"{BRAND_NAME_CN}")
    add_unique(cn12315)
    print(f"  12315: {len(cn12315)} 条")

    # 知乎
    print("\n💬 社交平台...")
    zh_results = search_zhihu(f"{BRAND_NAME_CN} 质量")
    add_unique(zh_results)
    print(f"  知乎: {len(zh_results)} 条")

    wb_results = search_weibo(f"{BRAND_NAME_CN} 质量")
    add_unique(wb_results)
    print(f"  微博(间接): {len(wb_results)} 条")

    return all_results


# ============================================================
# 结果分析与分类
# ============================================================

def analyze_results(results: List[Dict]) -> Dict[str, List[Dict]]:
    """对抓取结果做品牌相关性验证 + 负面检测 + 分级"""
    categorized = OrderedDict([
        ("🔴 高危预警", []),
        ("🟡 中危关注", []),
        ("🟢 低危提及", []),
        ("⚪ 中性/正面", []),
    ])

    for item in results:
        title = item.get("title", "")
        summary = item.get("summary", "")
        full_text = f"{title} {summary}"

        # 1. 品牌相关性验证
        if not contains_brand(full_text):
            continue  # 不相关的跳过

        # 2. 负面检测
        level, keywords, score = detect_negative(full_text)
        item["keywords"] = keywords
        item["score"] = score
        item["level"] = level

        # 3. 分类
        if "高危" in level:
            categorized["🔴 高危预警"].append(item)
        elif "中危" in level:
            categorized["🟡 中危关注"].append(item)
        elif "低危" in level:
            categorized["🟢 低危提及"].append(item)
        else:
            categorized["⚪ 中性/正面"].append(item)

    # 每类按危险分值降序排列
    for cat in categorized:
        categorized[cat].sort(key=lambda x: x.get("score", 0), reverse=True)

    return categorized


# ============================================================
# 邮件构建
# ============================================================

def build_email_html(categorized: Dict[str, List[Dict]], total_raw: int) -> str:
    """构建舆情监控邮件"""
    now = datetime.now(timezone(timedelta(hours=8)))
    total_neg = sum(len(v) for k, v in categorized.items() if "⚪" not in k)
    total_all = sum(len(v) for v in categorized.values())

    def escape(text):
        return html_lib.escape(text or "", quote=False)

    html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ margin:0; padding:0; background:#f0f2f5; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; }}
  .container {{ max-width: 700px; margin: 0 auto; padding: 20px; }}
  .header {{ background: linear-gradient(135deg, #8b0000 0%, #c0392b 50%, #e74c3c 100%); color:#fff; padding: 28px 24px; border-radius: 12px 12px 0 0; text-align: center; }}
  .header h1 {{ margin:0 0 4px 0; font-size: 24px; }}
  .header .brand {{ font-size: 18px; opacity: 0.9; margin: 4px 0; }}
  .header .time {{ opacity: 0.65; font-size: 12px; margin-top: 8px; }}
  .stats {{ background: #fff; padding: 14px 20px; border-bottom: 1px solid #e8e8e8; display: flex; justify-content: space-around; text-align: center; flex-wrap: wrap; }}
  .stats .stat-num {{ font-size: 26px; font-weight: bold; }}
  .stat-high {{ color: #c0392b; }}
  .stat-mid {{ color: #e67e22; }}
  .stat-low {{ color: #7f8c8d; }}
  .stat-label {{ font-size: 11px; color: #999; margin-top: 2px; }}
  .content {{ background: #fff; padding: 20px 24px; border-radius: 0 0 12px 12px; }}
  .section {{ margin-bottom: 24px; }}
  .section-title {{ font-size: 17px; font-weight: bold; padding-bottom: 6px; margin-bottom: 12px; border-bottom: 2px solid #e8e8e8; }}
  .section-title.high {{ color: #c0392b; border-color: #c0392b; }}
  .section-title.mid {{ color: #e67e22; border-color: #e67e22; }}
  .section-title.low {{ color: #7f8c8d; border-color: #7f8c8d; }}
  .section-title.neutral {{ color: #2c3e50; border-color: #2c3e50; }}
  .item {{ padding: 12px 0; border-bottom: 1px solid #f5f5f5; }}
  .item:last-child {{ border-bottom: none; }}
  .item .title {{ font-size: 14px; font-weight: 600; margin-bottom: 3px; }}
  .item .title a {{ color: #1a1a2e; text-decoration: none; }}
  .item .title a:hover {{ text-decoration: underline; color: #c0392b; }}
  .item .meta {{ font-size: 11px; color: #999; margin-bottom: 3px; }}
  .item .summary {{ font-size: 12px; color: #666; line-height: 1.5; }}
  .item .kws {{ margin-top: 4px; }}
  .kw-tag {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px; margin-right: 4px; margin-bottom: 2px; }}
  .kw-high {{ background: #ffe0e0; color: #c0392b; }}
  .kw-mid {{ background: #fff3e0; color: #e67e22; }}
  .kw-low {{ background: #f0f0f0; color: #888; }}
  .empty {{ color: #999; font-style: italic; text-align: center; padding: 16px; }}
  .no-alert {{ text-align: center; color: #27ae60; font-weight: bold; padding: 20px; font-size: 16px; }}
  .footer {{ text-align: center; color: #aaa; font-size: 11px; margin-top: 20px; line-height: 1.8; }}
  @media (max-width: 480px) {{
    .container {{ padding: 10px; }}
    .header {{ padding: 18px 12px; }}
    .content {{ padding: 12px 10px; }}
  }}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <h1>🛡️ 品牌舆情监控</h1>
  <p class="brand">监测品牌：{BRAND_NAME} / {BRAND_NAME_CN}</p>
  <p class="time">{now.strftime('%Y年%m月%d日 %H:%M')} (UTC+8)</p>
</div>

<div class="stats">
  <div><div class="stat-num stat-high">{len(categorized['🔴 高危预警'])}</div><div class="stat-label">🔴 高危</div></div>
  <div><div class="stat-num stat-mid">{len(categorized['🟡 中危关注'])}</div><div class="stat-label">🟡 中危</div></div>
  <div><div class="stat-num stat-low">{len(categorized['🟢 低危提及'])}</div><div class="stat-label">🟢 低危</div></div>
  <div><div class="stat-num" style="color:#2c3e50;">{total_all}</div><div class="stat-label">📊 总提取</div></div>
</div>

<div class="content">
"""

    # 如果没有负面信息
    if total_neg == 0:
        html += '<div class="no-alert">✅ 本轮未检测到品牌负面信息</div>\n'
        html += f'<p style="color:#888;text-align:center;">共扫描 {total_raw} 条结果，经品牌相关性过滤后 {total_all} 条</p>\n'

    # 分级展示
    section_configs = [
        ("🔴 高危预警", "high"),
        ("🟡 中危关注", "mid"),
        ("🟢 低危提及", "low"),
        ("⚪ 中性/正面", "neutral"),
    ]

    for section_name, css_class in section_configs:
        items = categorized.get(section_name, [])
        if section_name == "⚪ 中性/正面" and not items:
            continue  # 中性信息不强制显示

        category_class = "" if section_name == "⚪ 中性/正面" else "kw-high"
        if "中危" in section_name:
            category_class = "kw-mid"
        elif "低危" in section_name:
            category_class = "kw-low"

        html += f'<div class="section">\n<div class="section-title {css_class}">{section_name} ({len(items)})</div>\n'

        if not items:
            html += '<div class="empty">暂无</div>\n'
        else:
            for item in items:
                kw_tags_html = ""
                for kw in item.get("keywords", [])[:6]:
                    # 确定关键词标签样式
                    severity = "kw-low"
                    if kw in [k.lower() for k in NEGATIVE_HIGH]:
                        severity = "kw-high"
                    elif kw in [k.lower() for k in NEGATIVE_MEDIUM]:
                        severity = "kw-mid"
                    kw_tags_html += f'<span class="kw-tag {severity}">{escape(kw)}</span>'

                html += f"""
<div class="item">
  <div class="title">
    <a href="{escape(item.get('link', '#'))}" target="_blank">{escape(item.get('title', ''))}</a>
  </div>
  <div class="meta">📌 {escape(item.get('source', ''))} · {escape(item.get('type', ''))}</div>
  <div class="summary">{escape(item.get('summary', '')[:250])}</div>
  <div class="kws">{kw_tags_html}</div>
</div>
"""
        html += '</div>\n'

    html += f"""
</div><!-- .content -->
<div class="footer">
  <p>🛡️ <strong>Brand Monitor</strong> · {BRAND_NAME}/{BRAND_NAME_CN} 舆情监控</p>
  <p>运行在 GitHub Actions · 自动定时扫描 · 扫描了 {total_raw} 条原始结果</p>
  <p style="margin-top:4px;">⚠️ 自动系统结果，仅供参考，请人工核实</p>
</div>
</div><!-- .container -->
</body>
</html>
"""
    return html


def send_email(html_content: str, neg_count: int, high_count: int) -> bool:
    """发送舆情监控邮件"""
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD or not RECIPIENT_EMAIL:
        print("⚠️  邮件配置不完整，跳过发送")
        return False

    msg = MIMEMultipart("alternative")
    now = datetime.now(timezone(timedelta(hours=8)))

    # 高危告警标题特殊处理
    if high_count > 0:
        prefix = "🚨"
    elif neg_count > 0:
        prefix = "⚠️"
    else:
        prefix = "✅"

    subject = f"{prefix} {BRAND_NAME}舆情监控 · {now.strftime('%m/%d %H:%M')} · 负面{neg_count}条"
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((f"{BRAND_NAME} Monitor", EMAIL_ADDRESS))
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.qq.com", 465, context=context, timeout=30) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, [RECIPIENT_EMAIL], msg.as_string())
        print(f"\n✅ 邮件发送成功 → {RECIPIENT_EMAIL}")
        return True
    except Exception as e:
        print(f"\n❌ 邮件发送失败: {e}")
        return False


# ============================================================
# 缓存管理
# ============================================================

def load_cache() -> Dict[str, float]:
    """加载去重缓存"""
    cache_file = os.path.join(os.path.dirname(__file__), ".brand_cache.json")
    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache: Dict[str, float], new_items: List[Dict]):
    """保存缓存，标记已发送"""
    cache_file = os.path.join(os.path.dirname(__file__), ".brand_cache.json")
    now = datetime.now().timestamp()
    for item in new_items:
        key = hashlib.md5(item.get("title", "").encode()).hexdigest()
        cache[key] = now
    # 清理7天前的缓存
    cutoff = now - 7 * 86400
    cache = {k: v for k, v in cache.items() if v > cutoff}
    # 限制大小
    if len(cache) > 10000:
        sorted_items = sorted(cache.items(), key=lambda x: x[1], reverse=True)
        cache = dict(sorted_items[:10000])
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print(f"🛡️  {BRAND_NAME}/{BRAND_NAME_CN} 品牌舆情监控")
    print(f"    启动时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 加载缓存
    cache = load_cache()
    print(f"\n📋 缓存条目：{len(cache)} 条（过去7天）")

    # 2. 全网扫描
    print(f"\n{'='*60}")
    all_results = collect_all_results()
    raw_count = len(all_results)
    print(f"\n📊 原始采集：{raw_count} 条")

    # 3. 去重
    new_results = []
    for item in all_results:
        key = hashlib.md5(item["title"].encode()).hexdigest()
        if key not in cache:
            new_results.append(item)
    print(f"📊 去重后：{len(new_results)} 条新内容")

    # 4. 负面分析
    print(f"\n{'='*60}")
    print("🔬 品牌相关性 + 负面检测引擎...")
    categorized = analyze_results(new_results)

    total_neg = sum(len(v) for k, v in categorized.items() if "⚪" not in k)
    high_count = len(categorized.get("🔴 高危预警", []))
    mid_count = len(categorized.get("🟡 中危关注", []))
    low_count = len(categorized.get("🟢 低危提及", []))

    print(f"\n🔴 高危：{high_count} 条")
    print(f"🟡 中危：{mid_count} 条")
    print(f"🟢 低危：{low_count} 条")
    print(f"⚪ 中性：{len(categorized.get('⚪ 中性/正面', []))} 条")

    # 5. 构建并发送邮件
    if total_neg > 0 or len(new_results) > 0:
        html = build_email_html(categorized, raw_count)
        success = send_email(html, total_neg, high_count)
        if success:
            save_cache(cache, new_results)
    else:
        print("\n✅ 无新增内容，跳过发送。")

    print("\n" + "=" * 60)
    print("✅ 本轮监控完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
