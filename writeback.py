"""第 6 步：把一致性检查发现的问题写回 Notion 的「08 待合并」页。

只往待合并页末尾追加，不改原设定：AI 可能误判，最终由人确认（human-in-the-loop）。
同一段剧情只写一次：用剧情内容算出一个"检查编号"，写之前先看页面里有没有这个编号（幂等）。
"""
import hashlib
import json
from datetime import date
from pathlib import Path

from notion_loader import list_children, notion, rich_text_to_str

DATA = Path("data/settings.json")


def merge_page_id(novel):
    """在本地数据里找到这本小说的「08 待合并」页的 id。"""
    for r in json.loads(DATA.read_text(encoding="utf-8")):
        if r["novel"] == novel and r["section"].startswith("08"):
            return r["page_id"]
    raise ValueError(f"《{novel}》没有「08 待合并」页")


def check_id(novel, passage):
    """同一本书的同一段剧情 → 永远得到同一个编号。"""
    return hashlib.sha256(f"{novel}\n{passage}".encode("utf-8")).hexdigest()[:8]


def already_written(page_id, marker):
    """读一遍页面，看看这个编号是不是已经写过了。"""
    for block in list_children(page_id):
        rich = block.get(block["type"], {}).get("rich_text", [])
        if marker in rich_text_to_str(rich):
            return True
    return False


def text_block(kind, content):
    """生成一个 Notion block。Notion 规定一段文字最多 2000 字，超出的截掉。"""
    return {"object": "block", "type": kind, kind: {"rich_text": [{"type": "text", "text": {"content": content[:2000]}}]}}


def report_to_blocks(passage, report, marker):
    blocks = [
        text_block("heading_3", f"一致性检查 {date.today()}（{marker}）"),
        text_block("paragraph", f"被检查的剧情：{passage[:300]}{'……' if len(passage) > 300 else ''}"),
    ]
    if not report["conflicts"]:
        blocks.append(text_block("paragraph", "没有发现矛盾。"))
    for c in report["conflicts"]:
        flag = "" if c["verified"] else "（⚠️ 引用未核实，需人工确认）"
        blocks.append(text_block(
            "to_do",  # 用待办格式：人处理完一条就勾掉一条
            f"【{c['severity']}】{c['explanation']}{flag}\n"
            f"剧情：{c['passage_quote']}\n设定：{c['setting_quote']}（{c['source']}）\n建议：{c['suggestion']}",
        ))
    return blocks


def write_report(novel, passage, report):
    """把检查结果追加到待合并页。返回 True = 写了，False = 之前写过，跳过。"""
    page_id = merge_page_id(novel)
    marker = f"检查编号 {check_id(novel, passage)}"
    if already_written(page_id, marker):
        return False
    notion.blocks.children.append(block_id=page_id, children=report_to_blocks(passage, report, marker))
    return True


if __name__ == "__main__":
    from check import DEMO_NOVEL, DEMO_PASSAGE, check, print_report

    report = check(DEMO_NOVEL, DEMO_PASSAGE)
    print_report(report)
    for attempt in (1, 2):  # 故意写两次，验证第二次会被跳过（幂等）
        wrote = write_report(DEMO_NOVEL, DEMO_PASSAGE, report)
        print(f"\n第 {attempt} 次写入：{'已写入「08 待合并」' if wrote else '这段剧情之前写过了，跳过'}")
