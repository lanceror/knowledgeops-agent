"""第 3 步：把设定切成小块（chunk），再找出和问题最相关的几块。

流程：settings.json → 按标题切块 → 每块切成"两个字一组"的词 → BM25 打分 → 取前几名。
"""
import json
import math
import re
from collections import Counter
from pathlib import Path

DATA = Path("data/settings.json")
MAX_CHARS = 600  # 每块最多多少字：太大检索不准，太小信息不完整


def split_by_headings(text):
    """按 # / ## / ### 标题切段，并记住每段属于哪个标题（如"张三 > 基本信息"）。"""
    sections, stack, buf = [], [], []  # stack 里存 (标题级别, 标题文字)

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append((" > ".join(title for _, title in stack), body))
        buf.clear()

    for line in text.split("\n"):
        m = re.match(r"^(#{1,3}) (.+)", line)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:  # 遇到同级或更高级标题，先把旧的弹出去（坑 4：曾经写成 >）
                stack.pop()
            stack.append((level, m.group(2).strip()))
        else:
            buf.append(line)
    flush()
    return sections


def split_long(body):
    """一段超过 MAX_CHARS 时，按行再切开，尽量不把一句话切断。"""
    pieces, cur = [], ""
    for line in body.split("\n"):
        if cur and len(cur) + len(line) > MAX_CHARS:
            pieces.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        pieces.append(cur)
    return pieces


def load_chunks(path=DATA):
    chunks = []
    for page in json.loads(path.read_text(encoding="utf-8")):
        for heading, body in split_by_headings(page["text"]):
            for piece in split_long(body):
                chunks.append({
                    "novel": page["novel"],
                    "section": page["section"],
                    "heading": heading,
                    "text": piece,
                })
    return chunks


def tokenize(text):
    """中文按"相邻两个字"切：'张小明多高' → ['张小', '小明', '明多', '多高']。不需要词典，人名也能匹配上。"""
    tokens = []
    for run in re.findall(r"[一-鿿]+|[A-Za-z0-9]+", text.lower()):
        if run.isascii() or len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class Retriever:
    def __init__(self, chunks, k1=1.5, b=0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        # 标题信息也参与检索：问"张三身高"时，标题里的"张三"也能命中
        self.docs = [Counter(tokenize(f"{c['novel']} {c['section']} {c['heading']} {c['text']}")) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_len = sum(self.lengths) / len(self.lengths)
        df = Counter(tok for d in self.docs for tok in d)  # 每个词出现在几块里
        n = len(self.docs)
        # IDF：越少见的词越重要
        self.idf = {tok: math.log(1 + (n - f + 0.5) / (f + 0.5)) for tok, f in df.items()}

    def score(self, query_tokens, i):
        doc, length = self.docs[i], self.lengths[i]
        s = 0.0
        for tok in query_tokens:
            tf = doc.get(tok, 0)
            if tf:
                # 词频会饱和（k1），长块会被压分（b）
                s += self.idf[tok] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / self.avg_len))
        return s

    def search(self, query, novel=None, top_k=5):
        """novel 不为空时只在这本书里找：不同小说可能有同名角色，不能混在一起。"""
        q = tokenize(query)
        candidates = [i for i, c in enumerate(self.chunks) if novel is None or c["novel"] == novel]
        scored = sorted(((self.score(q, i), i) for i in candidates), reverse=True)
        return [(s, self.chunks[i]) for s, i in scored[:top_k] if s > 0]


if __name__ == "__main__":
    # 测试坑 4：两个同级标题不能嵌套在一起
    demo = "## 张三\n身高：180\n### 性格\n冷静\n## 李四\n身高：165"
    for heading, body in split_by_headings(demo):
        print(f"[{heading}] {body}")
    assert split_by_headings(demo)[2][0] == "李四", "同级标题被错误嵌套了！"
    print("坑 4 测试通过\n")

    chunks = load_chunks()
    sizes = [len(c["text"]) for c in chunks]
    print(f"共 {len(chunks)} 个 chunk，平均 {sum(sizes) // len(sizes)} 字，最长 {max(sizes)} 字")

    # 复现坑 5：同一个意思，换种问法（测试问题放在 data/ 里：私人数据，不上传）
    retriever = Retriever(chunks)
    queries = json.loads(Path("data/private_examples.json").read_text(encoding="utf-8"))["retriever_queries"]
    for query in queries:
        print(f"\n=== 搜索：{query} ===")
        for rank, (score, c) in enumerate(retriever.search(query, top_k=3), 1):
            hit = "  ← 有身高" if "身高" in c["text"] else ""
            print(f"{rank}. [{score:.2f}] {c['novel']} / {c['heading']}{hit}")
