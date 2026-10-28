"""第 1 步：确认能连上 Notion，并列出知识库的目录结构（只读，不改任何东西）。"""
import os

from dotenv import load_dotenv
from notion_client import APIResponseError, Client
from notion_client.helpers import collect_paginated_api

load_dotenv()  # 把 .env 里的内容读进环境变量，密钥不写死在代码里
token = os.getenv("NOTION_TOKEN", "")
root_id = os.getenv("NOTION_PAGE_ID", "")

if not token.startswith(("ntn_", "secret_")):
    raise SystemExit("NOTION_TOKEN 没填好：.env 里要放真实 token（ntn_ 开头）")
if not root_id:
    raise SystemExit("NOTION_PAGE_ID 没填：.env 里要放根页面的 id")

notion = Client(auth=token)


def child_pages(page_id):
    """返回某个页面下面的所有子页面：[(标题, id), ...]"""
    blocks = collect_paginated_api(notion.blocks.children.list, block_id=page_id)
    return [(b["child_page"]["title"], b["id"]) for b in blocks if b["type"] == "child_page"]


try:
    books = child_pages(root_id)
except APIResponseError as e:
    if e.code == "unauthorized":
        raise SystemExit("Token 无效：检查 .env 里的 NOTION_TOKEN 有没有复制完整")
    if e.code == "object_not_found":
        raise SystemExit("找不到页面：去 Notion 打开根页面 → 右上角 ··· → Connections → 添加你的 integration")
    raise

print(f"共找到 {len(books)} 个子页面：")
for title, book_id in books:
    sections = [t for t, _ in child_pages(book_id)]
    print(f"- {title}（{len(sections)} 个子页）: {', '.join(sections)}")
