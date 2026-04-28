#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 每日资讯日报 · 自动生成脚本
- 使用 DeepSeek API 生成深度日报
- 每天 8:30 由 launchd / GitHub Actions 自动触发
- 生成 HTML 日报 + 更新 index.html + git push 到 GitHub Pages
"""

import json
import os
import re
import subprocess
import sys
import warnings
from datetime import datetime

# 抑制 LibreSSL / urllib3 警告，避免 stderr 日志被刷屏
warnings.filterwarnings("ignore", category=UserWarning, module="urllib3")
try:
    from urllib3.exceptions import NotOpenSSLWarning
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except ImportError:
    pass

import requests

# ── 路径配置 ───────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
PROFILE_PATH = os.path.join(os.path.dirname(BASE_DIR), "profile.md")
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ── 读取配置 ───────────────────────────────────────────────
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

DEEPSEEK_API_KEY = config.get("deepseek_api_key", "")
GITHUB_TOKEN     = config.get("github_token", "")
GITHUB_USER      = config.get("github_username", "")
GITHUB_REPO      = config.get("github_repo", "ai-daily-news")
PAGES_BASE_URL   = config.get("pages_base_url", "")

# ── 读取 profile.md ────────────────────────────────────────
def load_profile() -> str:
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

# ── 新闻搜索（DuckDuckGo，免费无需 key）────────────────────
# 时间说明：
#   - 每天 8:30 触发，需覆盖"昨天全天 + 今天早间"，即过去 36 小时内容
#   - 中国夜间（UTC+8 22:00后）≈ 美国当日工作时间，所以用前一天日期也能捞到美国侧新闻
#
# 来源策略：
#   中文侧：量子位 / 机器之心 / 36kr / 虎嗅 / 极客公园 / 爱范儿 / 深响 / 硅星人
#   英文侧：TechCrunch / The Verge / Wired / VentureBeat / Bloomberg Tech
#   官方源：OpenAI / Anthropic / Google DeepMind / 腾讯 / 字节跳动 / 阿里 / TME

def _date_range_str() -> tuple[str, str]:
    """返回 (昨天中文日期, 今天中文日期) 用于搜索词"""
    from datetime import timedelta
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    return (
        yesterday.strftime("%Y年%m月%d日"),
        today.strftime("%Y年%m月%d日"),
    )

def _search_queries() -> list[str]:
    """动态生成搜索词列表，覆盖昨天+今天（36小时）"""
    yest, today = _date_range_str()
    ym = datetime.now().strftime("%Y年%m月")
    ym_en = datetime.now().strftime("%Y-%m")
    return [
        # ── 中文 · AI垂类自媒体（量子位/机器之心/36kr/虎嗅/极客公园/硅星人）──
        f"量子位 AI大模型 {today}",
        f"机器之心 AI {today}",
        f"36kr 人工智能 {today}",
        f"虎嗅 AI大厂 深度 {today}",
        f"极客公园 AI {today}",
        f"硅星人 AI {yest}",
        f"深响 音乐娱乐 AI {ym}",
        # ── 中文 · 互联网深度追踪媒体（晚点/暗涌/深燃）─────────────────────
        f"晚点LatePost 大厂 战略 {ym}",
        f"暗涌Waves 创业 互联网 {ym}",
        f"深燃 大厂 互联网 {today}",
        f"界面新闻 科技 大厂快讯 {today}",
        # ── 中文 · 大厂组织与人才动态 ─────────────────────────────────────
        f"腾讯 字节跳动 阿里 百度 AI 发布 {today}",
        f"大厂 AI 裁员 组织调整 人才 {ym}",
        f"腾讯音乐 TME 网易云音乐 AI {ym}",
        # ── 中文 · 音乐娱乐行业垂类媒体（小鹿角/音乐先声/娱乐资本论）────────
        f"音乐财经 小鹿角 音乐行业 {ym}",
        f"音乐先声 数字音乐 版权 {ym}",
        f"娱乐资本论 音乐 演出市场 {ym}",
        f"演出行业观察 音乐节 演唱会 {ym}",
        # ── 中文 · AI产品与法规 ────────────────────────────────────────────
        f"AI Agent 智能体 发布 {today}",
        f"大模型 国产 自研 {today}",
        f"音乐版权 AI 诉讼 授权 {ym}",
        # ── 英文 · AI Builder 自媒体/播客 ─────────────────────────────────
        f"Simon Willison LLM tools {ym_en}",
        f"swyx latent space AI engineer {ym_en}",
        f"Ethan Mollick AI work productivity {ym_en}",
        # ── 英文 · 垂类媒体（TechCrunch/The Verge/VentureBeat）──────────────
        f"site:techcrunch.com AI {ym_en}",
        f"site:theverge.com AI music streaming {ym_en}",
        f"site:venturebeat.com AI Agent {ym_en}",
        # ── 英文 · 海外大厂与音乐平台 ─────────────────────────────────────
        f"OpenAI Anthropic Google AI announcement {ym_en}",
        f"Spotify Apple Music AI feature {ym_en}",
    ]

def web_search(query: str) -> str:
    """DuckDuckGo 即时搜索，返回摘要文本（含 URL 以便溯源）"""
    try:
        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
        resp = requests.get(url, params=params, timeout=12)
        data = resp.json()
        parts = []
        if data.get("AbstractText"):
            src = data.get("AbstractURL", "")
            parts.append(f"{data['AbstractText']}\n来源：{src}" if src else data["AbstractText"])
        for item in data.get("RelatedTopics", [])[:5]:
            if isinstance(item, dict) and item.get("Text"):
                link = item.get("FirstURL", "")
                parts.append(f"{item['Text']}\n来源：{link}" if link else item["Text"])
        return "\n".join(parts)
    except Exception as e:
        return f"[搜索失败: {e}]"

def collect_news() -> str:
    """搜索所有关键词，汇总原始素材（覆盖过去 36 小时）"""
    print("📡 搜索新闻素材（覆盖昨天+今天，考虑全球时差）...", flush=True)
    yest, today = _date_range_str()
    results = [
        f"今天是 {today}，以下是覆盖 {yest}（昨天）至 {today}（今天）的 AI 相关新闻素材：\n"
    ]
    for q in _search_queries():
        text = web_search(q)
        if text.strip():
            results.append(f"【查询：{q}】\n{text}\n")
    return "\n".join(results)

# ── DeepSeek API 调用 ─────────────────────────────────────
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

def call_deepseek(system_prompt: str, user_prompt: str) -> str:
    """调用 DeepSeek-V3，含自动重试"""
    import time
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": 6000,   # 降低上限，减少长响应导致的连接中断
        "temperature": 0.7,
        "stream": False,
    }
    for attempt in range(4):  # 最多重试4次
        try:
            resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=180)
            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"⏳ 频率限制，{wait}秒后重试（第{attempt+1}次）...", flush=True)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("usage", {})
            input_tokens  = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            # DeepSeek-V3 官方价格：输入 ¥1/百万tokens，输出 ¥2/百万tokens
            cost = input_tokens / 1_000_000 * 1 + output_tokens / 1_000_000 * 2
            print(f"📊 Token 消耗：输入 {input_tokens:,} + 输出 {output_tokens:,} = {input_tokens+output_tokens:,} tokens | 本次费用约 ¥{cost:.4f}", flush=True)
            return data["choices"][0]["message"]["content"]
        except (requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError) as e:
            wait = 20 * (attempt + 1)
            print(f"⏳ 网络异常（{type(e).__name__}），{wait}秒后重试（第{attempt+1}次）...", flush=True)
            time.sleep(wait)
    raise Exception("DeepSeek API 重试4次均失败")

# ── System Prompt（注入 profile.md）──────────────────────
def build_system_prompt(profile: str) -> str:
    return f"""你是 WorkBuddy，Daisy 的专属 AI 日报助手。以下是关于 Daisy 的完整背景档案和日报规范，你必须严格遵守：

{profile}

---

## 你的任务
根据用户提供的今日新闻素材，生成一份完整的 AI 每日资讯日报 HTML 页面。

## HTML 输出规范（严格遵守）

### 整体结构
输出一个完整的 HTML 文件，包含：
1. `<!DOCTYPE html>` 开头
2. `<head>` 中包含完整 CSS 样式
3. 页面结构：header → stats-bar → container（正文）→ footer → back-btn

### 必须包含的 CSS 类（不得缺少）
```
.header, .header-badge, .header h1, .header .subtitle, .header .lead
.stats-bar, .stat, .stat-num, .stat-label
.container, .section-title
.news-card, .news-tag, .tag-music, .tag-product, .tag-org, .tag-legal, .tag-china, .tag-auto, .tag-warn
.news-card h3, .news-card p, .news-meta, .news-meta a
.highlight-box
.think-box, .think-box .think-label, .think-box h3, .think-box p, .think-box .highlight
.question-box, .question-box .q-label, .question-box p
.footer, .footer a
.back-btn, .back-btn:hover
```

### 配色规范
- 主色：#e94560（红）
- header/think 背景：linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)
- 高亮文字：#ffd700（金）
- 正文背景：#f5f5f7
- 卡片背景：#ffffff

### 必须包含的页面元素
1. **header**：badge + 日期标题 + subtitle + lead（今日导读，含金色关键判断）
2. **stats-bar**：3个核心数据（今日新闻议题数 + 2个今日关键数字）
3. **议题一**：AI 新产品/新功能（音乐娱乐/内容行业优先）
4. **议题二**：大厂组织动态/人才变化/业务调整
5. **思考延展**：严格2条，HRD视角，每条含观点+对Daisy的启发
6. **每日一问**：question-box，TME HRD一号位视角，有真实张力
7. **footer**：含 GitHub Pages 链接和"← 返回目录"文字链接
8. **back-btn**：右下角固定悬浮红色按钮"← 返回目录"，href="index.html"

### 链接规范
- 每条新闻 news-meta 必须包含来源链接
- 使用可靠域名：finance.sina.com.cn、news.qq.com、www.ithome.com、finance.eastmoney.com、36kr.com、www.sohu.com
- 禁止使用：so.html5.qq.com、new.qq.com（改用 news.qq.com）、仅域名无路径的链接

### 质量要求
- 只收录今天或昨天的新鲜内容，严格去重
- 思考延展必须结合当天具体新闻，不泛泛而谈
- 每条新闻80-150字，信息密度高
- 如果素材中某类新闻不足，宁缺毋滥，不用旧内容凑数

只输出完整 HTML 代码，不要加 markdown 代码块包裹，不要有任何解释文字。
"""

# ── 生成日报 HTML ──────────────────────────────────────────
def generate_html(news_raw: str, profile: str) -> str:
    today = datetime.now().strftime("%Y年%m月%d日")
    weekdays = ["周一","周二","周三","周四","周五","周六","周日"]
    weekday = weekdays[datetime.now().weekday()]
    pages_url = f"{PAGES_BASE_URL}/{datetime.now().strftime('%Y-%m-%d')}.html"

    system_prompt = build_system_prompt(profile)
    user_prompt = f"""今天是 {today}（{weekday}），请根据以下新闻素材生成完整日报 HTML。

GitHub Pages 链接（footer 和 back-btn 的 href 用 index.html）：{pages_url}

新闻素材：
{news_raw}

注意：
1. 日期标题写"{today} · {weekday}"
2. footer 中"在线版"链接写：{pages_url}
3. 所有"返回目录"的 href 统一用 index.html
4. 思考延展必须有2条，每条都要有对 Daisy（TME 培训经理/HRD）的具体启发
5. 每日一问必须有真实张力，不能有标准答案
"""
    print("✍️  DeepSeek 生成日报中...", flush=True)
    html = call_deepseek(system_prompt, user_prompt)
    # 清理可能的 markdown 代码块包裹
    html = re.sub(r'^```html\s*', '', html.strip())
    html = re.sub(r'\s*```$', '', html)
    return html

# ── 更新 index.html ────────────────────────────────────────
def update_index(date_str: str, html_content: str):
    """从日报 HTML 提取摘要，更新 index.html 归档列表"""
    # 提取 lead 内容作为摘要
    lead_match = re.search(r'class="lead"[^>]*>(.*?)</div>', html_content, re.S)
    lead_text = ""
    if lead_match:
        lead_text = re.sub(r'<[^>]+>', '', lead_match.group(1)).strip()[:60]

    index_path = os.path.join(BASE_DIR, "index.html")

    # 读取现有 index.html
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index_html = f.read()
    except Exception:
        index_html = ""

    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    date_cn = date_obj.strftime("%Y年%-m月%-d日")

    # 把原来的 latest-card 降级为 archive-item
    # 提取旧 latest-card 信息
    old_latest = re.search(
        r'<a class="latest-card" href="\./([^"]+)"[^>]*>.*?<div class="title">([^<]+)</div>',
        index_html, re.S
    )

    # 构建新 latest-card
    new_latest = f'''  <a class="latest-card" href="./{date_str}.html">
    <div class="left">
      <div class="tag">✨ 最新一期</div>
      <div class="title">{date_cn} · {lead_text[:40]}</div>
    </div>
    <div class="arrow">→</div>
  </a>

  <div class="section-label">往期回顾</div>

  <div class="archive-list">'''

    if old_latest:
        old_href = old_latest.group(1)
        old_title = old_latest.group(2)
        # 提取旧 href 对应日期
        old_date_match = re.match(r'(\d{4}-\d{2}-\d{2})', old_href)
        if old_date_match:
            old_date_obj = datetime.strptime(old_date_match.group(1), "%Y-%m-%d")
            old_date_cn = old_date_obj.strftime("%Y年%-m月%-d日")
        else:
            old_date_cn = old_href

        new_archive_item = f'''    <a class="archive-item" href="./{old_href}">
      <div>
        <div class="date">{old_date_cn}</div>
        <div class="lead">{old_title[:40]}</div>
      </div>
      <div class="arrow">→</div>
    </a>'''

        # 替换 latest-card 块，在 archive-list 开头插入旧条目
        index_html = re.sub(
            r'<a class="latest-card".*?</a>\s*<div class="section-label">往期回顾</div>\s*<div class="archive-list">',
            new_latest + "\n" + new_archive_item,
            index_html, flags=re.S
        )
    else:
        # index.html 不存在或格式不匹配，重建
        index_html = _build_index_html(date_str, date_cn, lead_text)

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_html)
    print(f"📋 index.html 已更新", flush=True)


def _build_index_html(date_str: str, date_cn: str, lead: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daisy 的 AI 每日资讯日报</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); min-height: 100vh; padding: 40px 20px; }}
  .container {{ max-width: 640px; margin: 0 auto; }}
  .hero {{ text-align: center; padding: 48px 0 40px; }}
  .hero-badge {{ display: inline-block; background: rgba(255,255,255,0.12); color: rgba(255,255,255,0.8); font-size: 12px; padding: 4px 16px; border-radius: 20px; margin-bottom: 16px; letter-spacing: 1px; }}
  .hero h1 {{ color: #fff; font-size: 28px; font-weight: 700; margin-bottom: 10px; }}
  .hero p {{ color: rgba(255,255,255,0.55); font-size: 14px; line-height: 1.8; }}
  .latest-card {{ background: #e94560; border-radius: 16px; padding: 24px 28px; margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between; text-decoration: none; transition: opacity 0.2s; }}
  .latest-card:hover {{ opacity: 0.9; }}
  .latest-card .left {{ }}
  .latest-card .tag {{ font-size: 11px; color: rgba(255,255,255,0.7); margin-bottom: 6px; letter-spacing: 1px; }}
  .latest-card .title {{ color: #fff; font-size: 17px; font-weight: 700; }}
  .latest-card .arrow {{ color: rgba(255,255,255,0.7); font-size: 22px; }}
  .section-label {{ font-size: 11px; color: rgba(255,255,255,0.4); letter-spacing: 2px; text-transform: uppercase; margin: 28px 0 14px 4px; }}
  .archive-list {{ display: flex; flex-direction: column; gap: 10px; }}
  .archive-item {{ background: rgba(255,255,255,0.07); border-radius: 12px; padding: 16px 20px; display: flex; align-items: center; justify-content: space-between; text-decoration: none; transition: background 0.2s; }}
  .archive-item:hover {{ background: rgba(255,255,255,0.12); }}
  .archive-item .date {{ color: rgba(255,255,255,0.9); font-size: 14px; font-weight: 600; }}
  .archive-item .lead {{ color: rgba(255,255,255,0.45); font-size: 12px; margin-top: 3px; }}
  .archive-item .arrow {{ color: rgba(255,255,255,0.3); font-size: 16px; }}
  .footer {{ text-align: center; padding: 40px 0 20px; color: rgba(255,255,255,0.25); font-size: 12px; }}
</style>
</head>
<body>
<div class="container">
  <div class="hero">
    <div class="hero-badge">🤖 AI DAILY · WorkBuddy</div>
    <h1>AI 每日资讯日报</h1>
    <p>音乐娱乐 · AI原生公司 · 互联网大厂动态<br>每日精选 · HRD 视角思考延展</p>
  </div>

  <a class="latest-card" href="./{date_str}.html">
    <div class="left">
      <div class="tag">✨ 最新一期</div>
      <div class="title">{date_cn} · {lead[:40]}</div>
    </div>
    <div class="arrow">→</div>
  </a>

  <div class="section-label">往期回顾</div>
  <div class="archive-list">
  </div>

  <div class="footer">WorkBuddy · 每日为你精选</div>
</div>
</body>
</html>"""


# ── Git Push ───────────────────────────────────────────────
def git_push(date_str: str) -> str:
    filename = f"{date_str}.html"
    remote = f"https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"
    cmds = [
        ["git", "-C", BASE_DIR, "add", filename, "index.html"],
        ["git", "-C", BASE_DIR, "commit", "-m", f"Auto: daily report {date_str}"],
        ["git", "-C", BASE_DIR, "push", remote, "main"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" not in r.stdout:
            print(f"⚠️  {' '.join(cmd[3:])}: {r.stderr.strip()}", flush=True)
    share_url = f"{PAGES_BASE_URL}/{filename}"
    print(f"🌐 已发布：{share_url}", flush=True)
    return share_url


# ── 主流程 ─────────────────────────────────────────────────
def main():
    print(f"\n{'='*52}", flush=True)
    print(f"🤖 AI 日报自动生成 | {datetime.now().strftime('%Y-%m-%d %H:%M')}", flush=True)
    print(f"{'='*52}\n", flush=True)

    # 周六（5）、周日（6）不推送
    weekday = datetime.now().weekday()
    if weekday >= 5:
        day_name = "周六" if weekday == 5 else "周日"
        print(f"📅 今天是{day_name}，按约定不生成日报，退出。", flush=True)
        return

    # 周一（0）：先生成日报，再自动触发周报
    if weekday == 0:
        print("📅 今天是周一，将在日报之后自动生成周报。", flush=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = os.path.join(BASE_DIR, f"{date_str}.html")

    # 已存在则跳过日报生成（但周一仍需触发周报）
    if os.path.exists(out_path):
        print(f"ℹ️  今日日报已存在，跳过生成：{out_path}", flush=True)
        if weekday == 0:
            run_weekly()
        return

    # 1. 读取 profile
    profile = load_profile()
    if not profile:
        print("⚠️  未找到 profile.md，将使用基础模式", flush=True)

    # 2. 搜索新闻
    news_raw = collect_news()

    # 3. 生成 HTML
    if not DEEPSEEK_API_KEY:
        print("❌ 未配置 deepseek_api_key，退出", flush=True)
        return  # 不用 sys.exit(1)，避免非零退出码导致 launchd 停止调度

    try:
        html = generate_html(news_raw, profile)
    except Exception as e:
        # API 全部失败时，把原始素材保存到 logs/ 供事后手动生成
        fail_path = os.path.join(LOG_DIR, f"{date_str}_raw_news.txt")
        with open(fail_path, "w", encoding="utf-8") as f:
            f.write(news_raw)
        print(f"❌ 日报生成失败：{e}", flush=True)
        print(f"📝 原始新闻素材已保存至：{fail_path}，可事后手动生成", flush=True)
        # 即使日报失败，周一仍尝试生成周报
        if weekday == 0:
            run_weekly()
        return  # 不用 sys.exit(1)，避免 launchd 停止调度

    # 4. 保存文件
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    # 同步到 logs/
    log_path = os.path.join(LOG_DIR, f"{date_str}.html")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"💾 已保存：{out_path}", flush=True)

    # 5. 更新 index.html
    update_index(date_str, html)

    # 6. Push
    if GITHUB_TOKEN and GITHUB_USER:
        git_push(date_str)
    else:
        print("⚠️  未配置 GitHub token，跳过发布", flush=True)

    print(f"\n✅ 完成！{PAGES_BASE_URL}/{date_str}.html", flush=True)

    # 周一额外触发周报
    if weekday == 0:
        run_weekly()


# ══════════════════════════════════════════════════════════
# 周报模块（每周一 09:00 自动触发）
# ══════════════════════════════════════════════════════════

def _weekly_search_queries(week_start: str, week_end: str) -> list[str]:
    """生成周报专用搜索词（约40条，时间窗口为过去7天）"""
    from datetime import timedelta
    now = datetime.now()
    ym = now.strftime("%Y年%m月")
    ym_en = now.strftime("%Y-%m")
    return [
        # ── 中文 · AI垂类自媒体（周维度）──────────────────────────────────
        f"量子位 大模型 {ym}",
        f"机器之心 AI {ym}",
        f"36kr 人工智能 {ym}",
        f"虎嗅 AI大厂 深度 {ym}",
        f"极客公园 AI {ym}",
        f"硅星人 AI {ym}",
        f"深响 音乐娱乐 AI {ym}",
        # ── 中文 · 互联网深度追踪媒体 ────────────────────────────────────
        f"晚点LatePost 大厂 战略 {ym}",
        f"暗涌Waves 创业 互联网 {ym}",
        f"深燃 大厂 互联网 {ym}",
        f"界面新闻 科技 大厂快讯 {ym}",
        # ── 中文 · 大模型发布与进展 ──────────────────────────────────────
        f"大模型 发布 {week_start} {week_end}",
        f"大模型 国产 自研 {ym}",
        f"AI Agent 智能体 发布 {ym}",
        f"OpenAI Anthropic Google 发布 {ym}",
        # ── 中文 · 硅谷&大厂组织动态 ─────────────────────────────────────
        f"腾讯 字节跳动 阿里 百度 AI 发布 {ym}",
        f"大厂 AI 裁员 组织调整 人才 {ym}",
        f"硅谷 AI 裁员 组织重组 {ym}",
        f"AI 公司 人才争夺 薪资 {ym}",
        f"科技公司 组织扁平化 去层级 {ym}",
        # ── 中文 · 音乐娱乐垂类 ─────────────────────────────────────────
        f"腾讯音乐 TME 网易云音乐 AI {ym}",
        f"音乐财经 小鹿角 音乐行业 {ym}",
        f"音乐先声 数字音乐 版权 {ym}",
        f"娱乐资本论 音乐 演出市场 {ym}",
        f"音乐版权 AI 诉讼 授权 {ym}",
        f"AI 音乐生成 版权 争议 {ym}",
        # ── 中文 · AI立法与治理 ───────────────────────────────────────────
        f"AI 法规 监管 立法 {ym}",
        f"人工智能 治理 伦理 政策 {ym}",
        # ── 英文 · AI旗舰模型与研究 ──────────────────────────────────────
        f"OpenAI GPT {ym_en}",
        f"Anthropic Claude {ym_en}",
        f"Google DeepMind Gemini {ym_en}",
        f"AI model release benchmark {ym_en}",
        # ── 英文 · 硅谷组织/HR动态 ───────────────────────────────────────
        f"tech layoffs AI automation {ym_en}",
        f"AI company restructuring workforce {ym_en}",
        f"Silicon Valley AI talent war salary {ym_en}",
        # ── 英文 · 娱乐&版权 ─────────────────────────────────────────────
        f"music AI copyright lawsuit {ym_en}",
        f"Spotify Apple Music AI feature {ym_en}",
        f"entertainment AI application {ym_en}",
        # ── 英文 · 垂类媒体 ───────────────────────────────────────────────
        f"site:techcrunch.com AI {ym_en}",
        f"site:theverge.com AI music {ym_en}",
        f"site:venturebeat.com AI enterprise {ym_en}",
    ]


def collect_weekly_news() -> tuple[str, str, str]:
    """搜索本周新闻素材，返回 (原始素材, 周开始日期字符串, 周结束日期字符串)"""
    from datetime import timedelta
    today = datetime.now()
    # 周报在周一触发，覆盖上周一到上周五（5天）
    week_end = today - timedelta(days=1)        # 上周日
    week_start = today - timedelta(days=7)       # 上上周一（宽松7天）
    ws = week_start.strftime("%Y年%m月%d日")
    we = week_end.strftime("%Y年%m月%d日")
    ws_en = week_start.strftime("%Y-%m-%d")
    we_en = week_end.strftime("%Y-%m-%d")

    print(f"📡 搜索周报新闻素材（{ws} — {we}）...", flush=True)
    results = [f"以下是 {ws} 至 {we} 的 AI 相关周度新闻素材：\n"]
    for q in _weekly_search_queries(ws, we):
        text = web_search(q)
        if text.strip():
            results.append(f"【查询：{q}】\n{text}\n")
    return "\n".join(results), ws_en, we_en


# ── 去重：历史话题管理 ────────────────────────────────────
WEEKLY_HISTORY_PATH = os.path.join(LOG_DIR, "weekly_history.json")
MAX_HISTORY_WEEKS = 4  # 保留最近4期


def load_weekly_history() -> dict:
    """读取历史周报标题，返回 {week_str: [title, ...]} 字典"""
    try:
        with open(WEEKLY_HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_weekly_history(week_str: str, titles: list[str]):
    """将本期标题追加到历史文件，保留最近 MAX_HISTORY_WEEKS 期"""
    history = load_weekly_history()
    history[week_str] = titles
    # 只保留最近 N 期
    if len(history) > MAX_HISTORY_WEEKS:
        oldest_key = sorted(history.keys())[0]
        del history[oldest_key]
    with open(WEEKLY_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"📝 已保存本期历史指纹（{len(titles)} 条标题）", flush=True)


def extract_titles_from_html(html: str) -> list[str]:
    """从生成的 HTML 中提取所有 <h3> 标题作为去重指纹"""
    titles = re.findall(r'<h3[^>]*>(.*?)</h3>', html, re.S)
    return [re.sub(r'<[^>]+>', '', t).strip() for t in titles if t.strip()]


def build_history_hint(history: dict) -> str:
    """将历史话题整理成 prompt 注入文本"""
    if not history:
        return ""
    lines = ["以下话题在近期周报中已详细报道，本期请跳过或仅用一句话简要提及（不展开）："]
    for week, titles in sorted(history.items()):
        for t in titles:
            lines.append(f"- {t}")
    return "\n".join(lines)


# ── 周报 System Prompt ────────────────────────────────────
def build_weekly_system_prompt(profile: str) -> str:
    return f"""你是 WorkBuddy，Daisy 的专属 AI 周报助手。以下是 Daisy 的完整背景档案：

{profile}

---

## 你的任务
根据用户提供的本周新闻素材，生成一份完整的 AI 每周快报 HTML 页面。

## 周报结构（严格遵守）

页面结构：header → stats-bar → container（正文）→ footer → back-btn

### container 内容顺序：
1. **三轨速览**（section-title："本周速览 · 三轨精华"）
   - 🧠 大模型进展（2-3条新闻卡片）
   - 🏢 硅谷 & AI-Native 组织动态（2-3条新闻卡片）
   - 🎵 娱乐行业 AI 应用 & 版权（2-3条新闻卡片）
   - 每个轨道用 `<div class="track-label">` 标注
2. **本周 HR 叙事线**（section-title："本周 HR 叙事线"）
   - 用 narrative-box，包含：n-label、h3（主题标题）、3段p（背景/分析/TME启发）
3. **思考延展**（section-title："思考延展"）
   - 严格2条 think-box，每条含延展标签、h3、p
   - 每条必须有对 Daisy（TME 培训经理）的具体启发
4. **HR 行动建议**（section-title："HR 行动建议"）
   - strategic action-box（面向HR负责人，战略层）
   - tactical action-box（面向培训经理，操作层，具体可落地）
5. **每周一问**（section-title："每周一问"）
   - question-box，必须有真实张力，不能有标准答案

## 必须包含的 CSS 类
```
.header, .header-badge, .header h1, .header .period, .header .keyword-block, .header .keyword-label, .header .keyword, .header .lead
.stats-bar, .stat, .stat-num, .stat-label
.container, .section-title, .track-label, .divider
.news-card, .news-tag（.tag-model .tag-org .tag-music .tag-legal .tag-china .tag-agent .tag-intl .tag-talent）
.news-card h3, .news-card p, .news-meta, .highlight-box
.narrative-box, .narrative-box .n-label, .narrative-box h3, .narrative-box p, .narrative-box .highlight
.think-box, .think-box .think-label, .think-box h3, .think-box p, .think-box .highlight
.action-box, .action-box.strategic, .action-box.tactical, .action-box .a-label, .action-box h4, .action-box p
.question-box, .question-box .q-label, .question-box p
.footer, .footer a, .back-btn
```

## 配色规范
- 主色：#e94560（红）
- header 背景：linear-gradient(135deg, #0d1b2a 0%, #1b2838 50%, #16324f 100%)
- 本周关键词：#ffd700（金色大字，28px，800weight）
- narrative/think 背景：深蓝渐变
- strategic action-box：#f0fff4 底色，绿色边框
- tactical action-box：#f0f4ff 底色，蓝色边框

## 信息源链接规范（必须严格遵守）
每条新闻卡片的 `.news-meta` 必须包含可点击的来源链接，格式如下：
```html
<div class="news-meta">来源：<a href="https://实际文章URL" target="_blank">媒体名称</a> · 日期</div>
```
- 链接必须是真实可访问的文章 URL，不得使用域名首页（如 https://36kr.com/ 这种纯首页链接不可用）
- 如果搜索素材中有具体 URL，优先使用；若无具体 URL，使用该媒体的搜索页或频道页（如 https://36kr.com/search/articles/AI）
- 禁止使用无效链接或 javascript:void(0)
- 每条新闻至少 1 个来源链接，可多个

## 去重与质量要求
- 严格执行用户提供的"已报道话题"列表，相关话题不再展开
- 每条新闻 80-150 字，信息密度高
- 本周关键词必须是1个动词短语（如「替代」「卡位」「加速」「渗透」）
- stats-bar 3个数字必须来自本周真实新闻，不得虚构
- 思考延展必须结合本周具体事件，不泛泛而谈
- 所有"返回目录"href 统一用 index.html

只输出完整 HTML 代码，不要加 markdown 代码块包裹，不要有任何解释文字。
"""


# ── 生成周报 HTML ─────────────────────────────────────────
def generate_weekly_html(news_raw: str, profile: str, week_str: str,
                          week_start: str, week_end: str, history_hint: str) -> str:
    """调用 DeepSeek 生成周报 HTML"""
    # 解析周次
    now = datetime.now()
    week_num = now.isocalendar()[1]
    pages_url = f"{PAGES_BASE_URL}/{week_str}-weekly.html"

    system_prompt = build_weekly_system_prompt(profile)
    history_section = f"\n\n【去重指令】\n{history_hint}" if history_hint else ""

    user_prompt = f"""本周是 {now.strftime('%Y')} 年第 {week_num} 周（{week_start} — {week_end}），请根据以下新闻素材生成完整周报 HTML。

GitHub Pages 链接：{pages_url}
周报文件名：{week_str}-weekly.html

新闻素材：
{news_raw}
{history_section}

注意：
1. header 标题写"AI 周报 · 第{week_num}周"，period 写"{week_start} — {week_end}"
2. footer 中"在线版"链接写：{pages_url}
3. 所有"返回目录"的 href 统一用 index.html
4. 思考延展严格2条，每条都要有对 Daisy（TME 培训经理/HRD）的具体启发
5. 每周一问必须有真实张力，不能有标准答案
6. 严格执行去重指令中列出的已报道话题，不再展开
"""
    print("✍️  DeepSeek 生成周报中...", flush=True)
    # 周报内容更多，适当提高 max_tokens
    payload_override = {"max_tokens": 8000}
    html = _call_deepseek_weekly(system_prompt, user_prompt)
    html = re.sub(r'^```html\s*', '', html.strip())
    html = re.sub(r'\s*```$', '', html)
    return html


def _call_deepseek_weekly(system_prompt: str, user_prompt: str) -> str:
    """调用 DeepSeek，周报专用（max_tokens=8000）"""
    import time
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": 8000,
        "temperature": 0.7,
        "stream": False,
    }
    for attempt in range(4):
        try:
            resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=240)
            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"⏳ 频率限制，{wait}秒后重试（第{attempt+1}次）...", flush=True)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("usage", {})
            input_tokens  = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            cost = input_tokens / 1_000_000 * 1 + output_tokens / 1_000_000 * 2
            print(f"📊 Token 消耗：输入 {input_tokens:,} + 输出 {output_tokens:,} | 费用约 ¥{cost:.4f}", flush=True)
            return data["choices"][0]["message"]["content"]
        except (requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError) as e:
            wait = 20 * (attempt + 1)
            print(f"⏳ 网络异常（{type(e).__name__}），{wait}秒后重试（第{attempt+1}次）...", flush=True)
            time.sleep(wait)
    raise Exception("DeepSeek API 重试4次均失败（周报）")


# ── 更新 index.html（周报卡片）────────────────────────────
def update_index_weekly(week_str: str, week_num: int, week_start: str, week_end: str, keyword: str = ""):
    """在 index.html 顶部（hero 下方）插入/更新周报绿色卡片"""
    index_path = os.path.join(BASE_DIR, "index.html")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index_html = f.read()
    except Exception:
        print("⚠️  未找到 index.html，跳过周报卡片更新", flush=True)
        return

    weekly_card = f'''  <a class="latest-card" href="./{week_str}-weekly.html" style="background: linear-gradient(135deg, #1a6b4a, #0d4a32); margin-bottom: 8px;">
    <div class="left">
      <div class="tag">📋 最新周报</div>
      <div class="title">第{week_num}周 · {week_start} — {week_end}</div>
    </div>
    <div class="arrow">→</div>
  </a>
'''

    # 如果已有周报卡片，替换它；否则插入到 hero 块之后
    if '📋 最新周报' in index_html:
        index_html = re.sub(
            r'  <a class="latest-card"[^>]*style="background: linear-gradient\(135deg, #1a6b4a.*?</a>\n',
            weekly_card,
            index_html, flags=re.S
        )
    else:
        # 插入到 </div>（hero块结束）之后，第一个 latest-card 之前
        index_html = re.sub(
            r'(  </div>\n\n)(  <a class="latest-card")',
            r'\1' + weekly_card + r'\n\2',
            index_html, count=1
        )

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_html)
    print(f"📋 index.html 周报卡片已更新", flush=True)


# ── 周报 Git Push ─────────────────────────────────────────
def git_push_weekly(week_str: str):
    filename = f"{week_str}-weekly.html"
    remote = f"https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"
    cmds = [
        ["git", "-C", BASE_DIR, "add", filename, "index.html", os.path.join("logs", "weekly_history.json")],
        ["git", "-C", BASE_DIR, "commit", "-m", f"Auto: weekly report {week_str}"],
        ["git", "-C", BASE_DIR, "push", remote, "main"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" not in r.stdout:
            print(f"⚠️  {' '.join(cmd[3:])}: {r.stderr.strip()}", flush=True)
    share_url = f"{PAGES_BASE_URL}/{week_str}-weekly.html"
    print(f"🌐 周报已发布：{share_url}", flush=True)
    return share_url


# ── 周报主流程 ────────────────────────────────────────────
def run_weekly():
    """周报主流程：搜索→去重→生成→保存→更新index→push"""
    print(f"\n{'─'*52}", flush=True)
    print(f"📅 周报模式启动 | {datetime.now().strftime('%Y-%m-%d %H:%M')}", flush=True)
    print(f"{'─'*52}\n", flush=True)

    now = datetime.now()
    week_num = now.isocalendar()[1]
    week_str = f"{now.strftime('%Y')}-W{week_num:02d}"
    out_path = os.path.join(BASE_DIR, f"{week_str}-weekly.html")

    # 已存在则跳过
    if os.path.exists(out_path):
        print(f"ℹ️  本周周报已存在，跳过生成：{out_path}", flush=True)
        return

    # 1. 读取 profile
    profile = load_profile()
    if not profile:
        print("⚠️  未找到 profile.md，将使用基础模式", flush=True)

    # 2. 搜索新闻
    news_raw, week_start, week_end = collect_weekly_news()

    # 3. 读取历史，构建去重提示
    history = load_weekly_history()
    history_hint = build_history_hint(history)
    if history_hint:
        print(f"🔍 已载入近 {len(history)} 期历史，去重指令已注入 prompt", flush=True)
    else:
        print("🔍 首期周报，无历史去重数据", flush=True)

    # 4. 生成 HTML
    if not DEEPSEEK_API_KEY:
        print("❌ 未配置 deepseek_api_key，退出", flush=True)
        return  # 不用 sys.exit(1)，避免 launchd 停止调度

    try:
        html = generate_weekly_html(news_raw, profile, week_str,
                                     week_start, week_end, history_hint)
    except Exception as e:
        fail_path = os.path.join(LOG_DIR, f"{week_str}_raw_news.txt")
        with open(fail_path, "w", encoding="utf-8") as f:
            f.write(news_raw)
        print(f"❌ 周报生成失败：{e}", flush=True)
        print(f"📝 原始素材已保存至：{fail_path}", flush=True)
        return  # 不用 sys.exit(1)，避免 launchd 停止调度

    # 5. 保存文件
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    log_path = os.path.join(LOG_DIR, f"{week_str}-weekly.html")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"💾 周报已保存：{out_path}", flush=True)

    # 6. 提取标题，保存历史指纹
    titles = extract_titles_from_html(html)
    save_weekly_history(week_str, titles)

    # 7. 更新 index.html
    update_index_weekly(week_str, week_num, week_start, week_end)

    # 8. Push
    if GITHUB_TOKEN and GITHUB_USER:
        git_push_weekly(week_str)
    else:
        print("⚠️  未配置 GitHub token，跳过发布", flush=True)

    print(f"\n✅ 周报完成！{PAGES_BASE_URL}/{week_str}-weekly.html", flush=True)


# ══════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    # 支持 --weekly 参数手动触发周报
    if "--weekly" in sys.argv:
        run_weekly()
    else:
        main()
