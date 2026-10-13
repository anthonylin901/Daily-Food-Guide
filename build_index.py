"""
前處理: PDF -> Markdown -> 切塊 -> Chroma 索引
執行: python build_index.py(只需要在資料有變動時執行)
"""

import base64

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_community.document_loaders import TextLoader
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import MarkdownHeaderTextSplitter

import config
import tables

"""
第一句：背景 → 告訴 Claude 這是什麼文件
編號規則 1～7 → 具體要怎麼轉換
"""
PAGE_PROMPT = """這是衛福部國民健康署《每日飲食指南手冊》的其中一頁。請把這一頁的內容完整轉成 Markdown：

1. 如果頁面側邊有直排的分類標籤（例如「全穀雜糧類」、「乳品類」），請把它當成最上方的一級標題 #。
2. 頁面中的標題用 ## 或 ###。
3. 表格轉成 Markdown 表格，保留表格標題、所有欄位名稱和數字。數字必須完全照原文，不可以更改或推算。
4. 表格的附註、註解都要保留。
5. 圖片上如果有文字說明（例如份量示意圖上的說明），請轉成文字；純裝飾的圖片不用描述。
6. 頁碼不要輸出。
7. 只輸出 Markdown 內容，不要加任何說明或開場白。"""

def pdf_to_markdown() -> None:
    import pymupdf
    from langchain.chat_models import init_chat_model
    from langchain_core.messages import HumanMessage

    pdf = pymupdf.open(config.PDF_PATH)
    config.PAGE_DIR.mkdir(parents=True,exist_ok=True)
    llm = None

    for i in range(len(pdf)):
        path = config.PAGE_DIR / f"page_{i + 1:02d}.md"
        if path.exists():
            continue
        if llm is None:
            llm = init_chat_model(config.VISION_MODEL)

        png = pdf[i].get_pixmap(dpi=150).tobytes("png")
        msg = HumanMessage(content=[
            {"type": "text", "text": PAGE_PROMPT},
            {"type": "image", "base64": base64.b64encode(png).decode(), "mime_type": "image/png"},
        ])
        path.write_text(llm.invoke([msg]).text, encoding="utf-8")
        print(f"number {i + 1} completed")

def load_pages() -> list[Document]:
    pages = []
    for path in sorted(config.PAGE_DIR.glob("page_*.md")):
        page_no = int(path.stem.split("_")[1])
        if page_no in config.SKIP_PAGES:
            continue
        doc = TextLoader(str(path), encoding="utf-8").load()[0]
        doc.metadata["page"] = page_no
        pages.append(doc)
    return pages

def split_pages(pages: list[Document]) -> list[Document]:
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "category"), ("##", "section"), ("###", "subsection")],
        strip_headers=False,
    )
    chunks = []
    current_category = ""

    for page in pages:
        for j, c in enumerate(splitter.split_text(page.page_content)):
            is_continuation = (
                    j == 0 and chunks
                    and "section" not in c.metadata and "subsection" not in c.metadata
            )
            if is_continuation:
                body = "\n".join(
                    line for line in c.page_content.splitlines() if not line.startswith("# ")
                ).strip()
                chunks[-1].page_content += body
                chunks[-1].metadata["page_end"] = page.metadata["page"]
                if "category" in c.metadata:
                    current_category = c.metadata["category"]
                continue

            c.metadata["page"] = page.metadata["page"]
            c.metadata["source"] = config.SOURCE_NAME
            if "category" in c.metadata:
                current_category = c.metadata["category"]
            else:
                c.metadata["category"] = current_category

            path = " > ".join(
                c.metadata[k] for k in ["category", "section", "subsection"] if c.metadata.get(k)
            )
            c.page_content = f"【{path}】\n{c.page_content}"
            chunks.append(c)

    return chunks

def make_table_docs() -> list[Document]:
    title = "如何使用我的每日飲食指南 > 表四 依熱量需求的六大類飲食建議份數"
    return [
        Document(
            page_content=f"【{title}】\n每日熱量需求 {kcal} 大卡的六大類飲食建議份數：{tables.servings_text(kcal)}。",
            metadata={
                "source": config.SOURCE_NAME,
                "page": tables.TABLE4_PAGE,
                "category": "如何使用我的每日飲食指南",
                "type": "table_sentence",
            },
        )
        for kcal in tables.KCALS
    ]

def get_vector_store() -> Chroma:
    embeddings = HuggingFaceEmbeddings(
        model_name=config.EMBED_MODEL,
        encode_kwargs={"normalize_embeddings": True},
    )
    return Chroma(
        collection_name=config.COLLECTION,
        embedding_function=embeddings,
        persist_directory=config.DB_DIR,
        collection_metadata={"hnsw:space": "cosine"},
    )

def build_index() -> int:
    docs = split_pages(load_pages()) + make_table_docs()
    store = get_vector_store()
    store.reset_collection()
    store.add_documents(docs, ids=[f"doc-{i}" for i in range(len(docs))])
    return len(docs)

if __name__ == "__main__":
    load_dotenv()
    print("步驟 1：PDF 轉 Markdown")
    pdf_to_markdown()
    print("步驟 2～5：切塊並建立索引")
    n = build_index()
    print(f"完成，共 {n} 塊")