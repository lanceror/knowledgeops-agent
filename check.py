"""第 5 步：一致性检查 Agent。

给它一段剧情，它会：拆出里面的事实点 → 逐个去设定库搜 → 对比 → 用工具提交结构化的冲突清单。
最后由代码再核对一遍：模型引用的"设定原文"必须真的出现在搜索结果里，否则标记为"未核实"。
"""
import json
import re
import sys
from pathlib import Path

import anthropic

from ask import MODEL, NOVELS, retriever

MAX_STEPS = 10  # 一段剧情要查好几个事实点，比问答多给几轮

SYSTEM = """你是小说的设定校对员。用户会给你一段剧情和它所属的小说。
你的任务：找出这段剧情里和已有设定**矛盾**的地方。

步骤：
1. 先列出剧情里涉及设定的事实点（人物身份、年龄、身高、外貌、关系、经历、时间线、世界观规则等）。
2. 用 search_settings 逐个去设定库里查。关键词要具体，如"张三 身高""李四 职位"。
3. 全部查完后，必须调用 report_conflicts 提交结果，不要直接用文字回答。

判断标准：
- 只有设定里**明确写了**、并且和剧情说法对不上，才算矛盾。
- 设定里没写的内容，不算矛盾（那是新增内容，不是错误）。
- setting_quote 必须**一字不改**地复制搜索结果里的原文，不能自己概括。"""

TOOLS = [
    {
        "name": "search_settings",
        "description": "在这本小说的设定库里做关键词搜索，返回最相关的几段设定原文。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词，如 '张三 身高'"}},
            "required": ["query"],
        },
    },
    {
        # 用"提交报告"工具的参数格式，强制模型按固定结构交结果（结构化输出）
        "name": "report_conflicts",
        "description": "全部检查完后调用，提交最终结果。没有矛盾就传空列表。",
        "input_schema": {
            "type": "object",
            "properties": {
                "checked_points": {
                    "type": "array", "items": {"type": "string"},
                    "description": "检查过的所有事实点（包括没问题的）",
                },
                "conflicts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "passage_quote": {"type": "string", "description": "剧情里有问题的原句"},
                            "setting_quote": {"type": "string", "description": "设定里对应的原文（一字不改）"},
                            "source": {"type": "string", "description": "设定出处，如 '02 人物 / 张三 > 基本信息'"},
                            "severity": {"type": "string", "enum": ["明确矛盾", "可能矛盾"]},
                            "explanation": {"type": "string", "description": "为什么矛盾，一句话"},
                            "suggestion": {"type": "string", "description": "建议怎么改剧情"},
                        },
                        "required": ["passage_quote", "setting_quote", "source", "severity", "explanation", "suggestion"],
                    },
                },
            },
            "required": ["checked_points", "conflicts"],
        },
    },
]


def _normalize(s):
    """去掉空白和列表符号，方便比对引用是否真的出自原文。"""
    return re.sub(r"[\s\-•·]", "", s)


def _as_list(value):
    """坑 7：模型偶尔会把数组写成一段 JSON 字符串（"[{...}]"），这里统一转回列表。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [value]
    return value if isinstance(value, list) else [value]


def parse_report(raw):
    """把模型交上来的结果整理成干净的格式；格式不对的条目丢掉，而不是让程序崩溃。"""
    conflicts = []
    for item in _as_list(raw.get("conflicts", [])):
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except json.JSONDecodeError:
                continue
        if isinstance(item, dict) and item.get("setting_quote"):
            conflicts.append(item)
    points = [str(p) for p in _as_list(raw.get("checked_points", []))]
    return {"checked_points": points, "conflicts": conflicts}


def check(novel, passage, client=None, verbose=True):
    if novel not in NOVELS:
        raise ValueError(f"找不到小说《{novel}》，可选：{'、'.join(NOVELS)}")
    client = client or anthropic.Anthropic()
    messages = [{"role": "user", "content": f"小说：《{novel}》\n\n待检查的剧情：\n{passage}"}]
    seen_texts = []  # 所有搜到过的设定原文，用来核对模型的引用（坑 8）

    for _ in range(MAX_STEPS):
        # 旧版用 tool_choice={"type": "any"} 强制调用工具，但新模型不支持（400 报错），改成下面的兜底
        resp = client.messages.create(model=MODEL, max_tokens=4096, system=SYSTEM, tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        if not any(b.type == "tool_use" for b in resp.content):
            # 模型直接用文字回答了，没有按格式提交 → 提醒它用工具提交，再来一轮
            if verbose:
                print("  [提醒] 模型没有用工具提交结果，要求它重新提交")
            messages.append({"role": "user", "content": "请调用 report_conflicts 工具提交结果（没有矛盾就传空列表），不要直接用文字回答。"})
            continue
        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            if block.name == "report_conflicts":
                Path("data/last_check.json").write_text(  # 留一份原始结果，方便排查问题
                    json.dumps(block.input, ensure_ascii=False, indent=2), encoding="utf-8")
                report = parse_report(block.input)
                for c in report["conflicts"]:
                    # 坑 8：引用的原文必须真的在搜索结果里出现过，否则可能是模型编的
                    c["verified"] = any(_normalize(c["setting_quote"]) in _normalize(t) for t in seen_texts)
                return report
            # search_settings：书名由代码固定，不让模型搜到别的书里去
            if verbose:
                print(f"  [搜索] {block.input['query']}")
            results = retriever.search(block.input["query"], novel, top_k=4)
            seen_texts.extend(c["text"] for _, c in results)
            text = "\n\n".join(f"[{c['section']} / {c['heading']}]\n{c['text']}" for _, c in results) or "没有找到相关设定。"
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": text})
        messages.append({"role": "user", "content": tool_results})
    raise RuntimeError("检查轮数用完了，没有得到结果")


def print_report(report):
    print(f"\n检查了 {len(report['checked_points'])} 个事实点，发现 {len(report['conflicts'])} 处矛盾：")
    for i, c in enumerate(report["conflicts"], 1):
        flag = "" if c["verified"] else "  ⚠️ 引用未在设定原文中找到，需人工确认"
        print(f"\n{i}. 【{c['severity']}】{c['explanation']}{flag}")
        print(f"   剧情：{c['passage_quote']}")
        print(f"   设定：{c['setting_quote']}  （{c['source']}）")
        print(f"   建议：{c['suggestion']}")


# 自测用的剧情（故意埋了几处和设定矛盾的地方）放在 data/ 里：那是私人数据，不上传
_examples = json.loads(Path("data/private_examples.json").read_text(encoding="utf-8"))
DEMO_NOVEL, DEMO_PASSAGE = _examples["demo_novel"], _examples["demo_passage"]

if __name__ == "__main__":
    if len(sys.argv) == 3:  # python check.py 书名 剧情.txt
        novel, passage = sys.argv[1], open(sys.argv[2], encoding="utf-8").read()
    else:
        novel, passage = DEMO_NOVEL, DEMO_PASSAGE
        print(f"使用自测剧情（埋了几处错误）：\n{passage}\n")
    report = check(novel, passage)
    print_report(report)
