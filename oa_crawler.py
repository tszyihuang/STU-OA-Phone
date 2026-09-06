# -*- coding: utf-8 -*-
"""
汕头大学 OA（泛微 ecology）公开目录爬虫
======================================

目标：http://oa.stu.edu.cn/login/Login.jsp?logintype=1
这是一个对外公开的 OA 目录（csweb/list.jsp），可无需登录访问。

交互方式：
  1. 启动后先抓取并打印第一页目录；
  2. 输入条目序号   -> 抓取并打印该条目的正文
     输入 n / next  -> 下一页
     输入 p / prev  -> 上一页
     输入 g / goto  -> 跳转到指定页
     输入 r / reload-> 重新加载当前页
     输入 q / quit  -> 退出

用法：python oa_crawler.py
依赖：pip install requests beautifulsoup4
"""

import re
import sys

import requests
from bs4 import BeautifulSoup, Comment, NavigableString

BASE = "http://oa.stu.edu.cn"
LIST_URL = BASE + "/csweb/list.jsp"
DOC_URL = BASE + "/page/maint/template/news/newstemplateprotal.jsp"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": BASE + "/login/Login.jsp?logintype=1",
}

# 块级标签：提取正文时在其后换行；其余内联标签不打断行
BLOCK_TAGS = {
    "p", "div", "br", "tr", "td", "li", "h1", "h2", "h3", "h4",
    "h5", "h6", "table", "ul", "ol", "hr", "blockquote",
}
DISCARD_TAGS = ["script", "style", "input", "button", "iframe"]


def setup_console():
    """让中文能在 Windows 控制台正常显示（不影响 stdin）。"""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def extract_text(node):
    """按段落提取节点文本。Comment 是 NavigableString 子类，必须显式跳过，
    否则 Word 条件注释（<!--[if gte mso N]>...<![endif]-->）会被当作正文。"""
    parts = []
    for desc in node.descendants:
        if isinstance(desc, Comment):
            continue
        parts.append(str(desc) if isinstance(desc, NavigableString)
                     else "\n" if getattr(desc, "name", "") in BLOCK_TAGS else "")
    lines = [l.strip() for l in "".join(parts).split("\n")]
    return [l for l in lines if l]


class OACrawler:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.page, self.pagesize = 1, 10
        self.total_pages, self.total_count = None, None
        self.fwdw, self.keyword = "-1", ""
        self.items = []
        self.cache = {}  # docid -> 正文行列表，重复打开不再下载

    def _soup(self, url, data=None):
        """统一请求入口：请求 + GBK 解码 + 解析，异常向上抛。"""
        resp = (self.session.post(url, data=data, timeout=30) if data
                else self.session.get(url, timeout=30))
        resp.raise_for_status()
        return BeautifulSoup(resp.content.decode("gb18030", "replace"), "html.parser")

    # -- 目录 --------------------------------------------------------------
    def goto_page(self, page):
        """抓取并显示某一页目录，返回是否成功。"""
        if self.total_pages:
            page = max(1, min(page, self.total_pages))
        self.page = page
        try:
            soup = self._soup(LIST_URL, {
                "pageindex": str(page),
                "pagesize": str(self.pagesize),
                "totalcount": str(self.total_count or ""),
                "totalindex": str(self.total_pages or ""),
                "keyword": self.keyword,
                "fwdw": self.fwdw,
            })
            self._parse_list(soup)
            self._show_list()
            return True
        except requests.RequestException as exc:
            print(f"  抓取目录失败：{exc}")
            return False

    def _parse_list(self, soup):
        self.items = [
            {
                "docid": re.search(r"docid=(\d+)", a["href"]).group(1),
                "title": (a.get("title") or a.get_text(" ", strip=True)).strip(),
                "unit": (tds[1].get_text(" ", strip=True) if len(tds) > 1 else ""),
                "date": (tds[2].get_text(" ", strip=True) if len(tds) > 2 else ""),
            }
            for a in soup.select('tr.datalight a[href*="docid="]')
            for tds in [a.find_parent("tr").find_all("td")]
        ]
        # 只对分页那一小块文本做一次正则，拿到总数/页数/当前页
        # （先把节点里的换行/&nbsp; 统一压成空格，避免 . 跨不过换行）
        info = soup.find(string=re.compile(r"共\s*\d+\s*条记录"))
        m = re.search(r"共\s*(\d+)\s*条记录.*?每页\s*(\d+)\s*条.*?共\s*(\d+)\s*页.*?当前第\s*(\d+)\s*页",
                      re.sub(r"\s+", " ", info) if info else "")
        if m:
            self.total_count, self.pagesize, self.total_pages, self.page = map(int, m.groups())

    def _show_list(self):
        print("=" * 72)
        print(f"  第 {self.page} 页 / 共 {self.total_pages} 页    "
              f"（共 {self.total_count} 条记录，每页 {self.pagesize} 条）")
        print("=" * 72)
        for i, it in enumerate(self.items, 1):
            print(f"  [{i:>2}] {it['title']}")
            print(f"       发布单位：{it['unit']}    发布日期：{it['date']}")
        print("-" * 72)

    # -- 正文 --------------------------------------------------------------
    def get_content(self, docid):
        """抓取并提取正文；已抓过的 docid 直接返回缓存。"""
        if docid not in self.cache:
            span = self._soup(f"{DOC_URL}?templatetype=1&templateid=3&docid={docid}")\
                       .find(id="spanContent")
            lines = []
            if span:
                for c in span.find_all(string=lambda s: isinstance(s, Comment)):
                    c.extract()  # 整块移除 Word/OOXML 条件注释（mso 9/10…任意版本）
                for tag in span.find_all(DISCARD_TAGS):
                    tag.decompose()
                lines = extract_text(span)
            self.cache[docid] = lines
        return self.cache[docid]

    def open_item(self, index):
        if not (1 <= index <= len(self.items)):
            print("  序号超出范围，请重新输入。")
            return
        it = self.items[index - 1]
        print("=" * 72)
        print(f"  正在抓取正文：{it['title']}")
        print(f"  发布单位：{it['unit']}    发布日期：{it['date']}")
        print("=" * 72)
        try:
            lines = self.get_content(it["docid"])
        except requests.RequestException as exc:
            print(f"  抓取失败：{exc}")
            return
        if lines:
            print("\n".join("  " + l for l in lines))
        else:
            print("  （未获取到正文，可能是权限限制或页面结构变化）")
        print("-" * 72)
        print("  （正文结束，按回车返回目录...）")
        input()
        self._show_list()


def main():
    setup_console()
    crawler = OACrawler()
    if not crawler.goto_page(1):
        print("  无法访问 OA 目录，请检查网络后重试。")
        sys.exit(1)

    print("操作提示：输入序号打开条目并打印正文；n 下一页；p 上一页；")
    print("          g 跳转页；r 重新加载；q 退出。")

    while True:
        try:
            raw = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  已退出。")
            break
        if not raw:
            continue

        match raw.lower():
            case "q" | "quit" | "exit":
                print("  已退出。")
                break
            case "n" | "next":
                crawler.goto_page(crawler.page + 1)
            case "p" | "prev":
                crawler.goto_page(crawler.page - 1)
            case "r" | "reload":
                crawler.goto_page(crawler.page)
            case "g" | "goto":
                try:
                    crawler.goto_page(int(input("    跳转到第几页：").strip()))
                except (ValueError, EOFError):
                    print("    输入无效。")
            case _ if raw.isdigit():
                crawler.open_item(int(raw))
            case _:
                print("  无法识别该指令，请输入条目序号 / n / p / g / r / q。")


if __name__ == "__main__":
    main()
