"""第 2 步：把 Notion 设定页读成纯文本，存到 data/settings.json（RAG 的第一环：数据加载）。

后面的检索和问答都只读这个文件，不用每次都去请求 Notion。
"""
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from notion_client import APIResponseError, Client
from notion_client.helpers import collect_paginated_api

load_dotenv()
notion = Client(auth=os.environ["NOTION_TOKEN"])
ROOT_ID = os.environ["NOTION_PAGE_ID"]
OUTPUT = Path("data/settings.json")

# "正文"是小说本身、很长，不算"设定"；之后做一致性检查时会单独读取
SKIP_TITLES = {"正文"}

# 这些类型的 block 里有文字（存在 rich_text 字段里）
TEXT_BLOCKS = {
    "paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item",
    "numbered_list_item", "to_do", "toggle", "quote", "callout", "code",
}
# 加个前缀，保留"这是标题 / 这是列表"的结构信息，后面切块要用
PREFIX = {
    "heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
    "bulleted_list_item": "- ", "numbered_list_item": "- ", "to_do": "- ",
}
# 这些 block 的子内容在视觉上是"缩进一层"的
LIST_LIKE = {"bulleted_list_item", "numbered_list_item", "to_do", "toggle"}


def list_children(block_id):
    """读取一个页面/block 下面的所有子 block（自动翻页）；遇到 Notion 限流就等一会儿重试。"""
    for attempt in range(5):
        try:
            return collect_paginated_api(notion.blocks.children.list, block_id=block_id)
        except APIResponseError as e:
            if e.code == "rate_limited":  # 只有限流值得重试；token 错了重试也没用
                time.sleep(2 ** attempt)  # 1, 2, 4, 8, 16 秒：指数退避
                continue
            raise
    raise RuntimeError("Notion 一直在限流，过几分钟再试")


def rich_text_to_str(rich_text):
    """Notion 的一段文字会被拆成很多小段（加粗、链接各算一段），拼回去。"""
    return "".join(part["plain_text"] for part in rich_text)


def block_to_lines(block, depth=0):
    """把一个 block（连同它所有的子 block）转成几行纯文本。"""
    kind = block["type"]
    lines = []

    if kind == "child_page":  # 设定页里还嵌了子页面：标题当小标题，内容一起读进来
        lines.append(f"## {block['child_page']['title']}")
    elif kind == "table_row":
        lines.append(" | ".join(rich_text_to_str(cell) for cell in block["table_row"]["cells"]))
    elif kind in TEXT_BLOCKS:
        text = rich_text_to_str(block[kind].get("rich_text", []))
        if text:
            lines.append("  " * depth + PREFIX.get(kind, "") + text)

    # 关键：列表、折叠块、表格、分栏都可能藏着子 block → 递归往下读
    if block.get("has_children") and kind != "child_database":
        child_depth = depth + 1 if kind in LIST_LIKE else depth
        for child in list_children(block["id"]):
            lines.extend(block_to_lines(child, child_depth))
    return lines


def page_text(page_id):
    lines = []
    for block in list_children(page_id):
        lines.extend(block_to_lines(block))
    return "\n".join(lines)


def main():
    records = []
    novels = [b for b in list_children(ROOT_ID) if b["type"] == "child_page"]
    for novel in novels:
        novel_title = novel["child_page"]["title"]
        print(f"读取《{novel_title}》...")
        for page in list_children(novel["id"]):
            if page["type"] != "child_page":
                continue
            section = page["child_page"]["title"]
            if section in SKIP_TITLES:
                continue
            text = page_text(page["id"])
            # 每一页存成一条记录：来自哪本书、哪个设定页、页面 id（以后写回 Notion 要用）
            records.append({"novel": novel_title, "section": section, "page_id": page["id"], "text": text})
            print(f"  {section}: {len(text)} 字")

    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(r["text"]) for r in records)
    empty = sum(1 for r in records if not r["text"].strip())
    print(f"\n完成：{len(records)} 个设定页（其中 {empty} 页是空的），共 {total} 字 → {OUTPUT}")


if __name__ == "__main__":
    main()
