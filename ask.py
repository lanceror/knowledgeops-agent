"""第 4 步：设定问答 Agent（agentic RAG）。

和普通 RAG 的区别：不是"搜一次就答"，而是把搜索做成工具交给 Claude，
让它自己决定搜什么关键词、搜不到就换个说法再搜，最后只根据搜到的设定回答并注明出处。
"""
import json
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from retriever import Retriever, load_chunks

load_dotenv(override=True)  # 系统环境变量里还有旧 key，让 .env 优先
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")  # 模型名可配置，方便换模型对比
MAX_STEPS = 6  # 代码层面的硬上限：最多来回几轮，防止模型一直搜下去、烧钱

retriever = Retriever(load_chunks())
NOVELS = sorted({c["novel"] for c in retriever.chunks})

SYSTEM = f"""你是"小说设定管家"，只根据设定库回答问题。
设定库里的小说：{"、".join(NOVELS)}。

规则：
1. 回答前必须先用 search_settings 搜索。搜索是按关键词匹配的，所以要把问题改写成设定里可能出现的词，
   例如"张三多高"要搜"张三 身高"。搜不到就换个说法再搜。
2. 问题提到了哪本书，就用 novel 参数只在那本书里搜（不同书可能有同名角色）。
3. 只使用搜索结果里的信息，不要编造。设定里确实没有，就直接说"设定里没有写"。
4. 同一个问题最多搜索 3 次。3 次都没找到相关内容，就停下来回答"设定里没有写"。
5. 回答简洁，末尾列出依据，格式：依据：[书名 / 设定页 / 标题]"""

TOOLS = [{
    "name": "search_settings",
    "description": "在小说设定库里做关键词搜索，返回最相关的几段设定原文。关键词越具体越好，可以多次调用。",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词，例如 '张三 身高'"},
            "novel": {"type": "string", "enum": NOVELS, "description": "只在这本书里搜（可选）"},
        },
        "required": ["query"],
    },
}]


def search_settings(query, novel=None):
    """真正执行搜索的是我们的代码，不是模型：模型只负责说"我要搜什么"。"""
    results = retriever.search(query, novel, top_k=5)
    if not results:
        return "没有找到相关设定。可以换个关键词再试。"
    return "\n\n".join(f"[{c['novel']} / {c['section']} / {c['heading']}]\n{c['text']}" for _, c in results)


def ask(question, client=None, verbose=True):
    """回答一个问题，返回 (答案, 搜索记录, token 用量)。"""
    client = client or anthropic.Anthropic()
    messages = [{"role": "user", "content": question}]
    searches = []
    usage = {"input": 0, "output": 0}
    for _ in range(MAX_STEPS):
        resp = client.messages.create(model=MODEL, max_tokens=1024, system=SYSTEM, tools=TOOLS, messages=messages)
        usage["input"] += resp.usage.input_tokens
        usage["output"] += resp.usage.output_tokens
        if resp.stop_reason != "tool_use":  # 模型不再要工具 = 给出最终答案
            return "".join(b.text for b in resp.content if b.type == "text"), searches, usage

        # 模型要调用工具：把它的请求记进对话，执行搜索，再把结果发回去
        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                searches.append(block.input)
                if verbose:
                    print(f"  [搜索] {block.input}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,  # 对上号：这是哪一次工具调用的结果
                    "content": search_settings(**block.input),
                })
        messages.append({"role": "user", "content": tool_results})
    return "（搜索轮数用完了，没能得出答案）", searches, usage


if __name__ == "__main__":
    # 默认测试问题放在 data/ 里（私人数据，不上传）；也可以在命令行直接写问题
    question = " ".join(sys.argv[1:]) or json.loads(Path("data/private_examples.json").read_text(encoding="utf-8"))["ask_question"]
    print(f"问题：{question}（模型：{MODEL}）")
    answer, searches, usage = ask(question)
    print(f"\n{answer}")
    print(f"\n（搜索 {len(searches)} 次，输入 {usage['input']} token，输出 {usage['output']} token）")
