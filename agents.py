"""
建立 agent，並讓多個模型同時回答同一個問題
"""
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from langchain.agents import create_agent

import config
from tools import ALL_TOOLS

load_dotenv()

SYSTEM_PROMPT = """你是一位營養飲食助理，根據衛福部國民健康署《每日飲食指南手冊》和 Open Food Facts 食品資料庫，回答使用者的飲食問題。

規則：
1. 回答前一定要用工具查詢資料，只能根據查到的內容回答，不要自行推測數字。
2. 使用者提到具體的每日熱量（大卡）時，用 get_daily_servings 查詢建議份數；
   使用者提供性別、年齡、活動強度時，先用 get_calorie_needs 查出熱量，再用 get_daily_servings 查份數。
3. 一般的飲食原則、營養知識、份量代換，用 search_guide 查詢手冊。
4. 市售包裝食品的營養成分，用 get_food 查詢。查不到時如實告知，並請使用者提供包裝上的營養標示。
5. Open Food Facts 的數值是每 100 公克，回答時要說清楚；若使用者給了實際吃的重量，請幫忙換算。
6. 在回答最後標出資料來源，例如「（每日飲食指南手冊 第 11 頁）」或「（Open Food Facts）」。
7. 這是一般飲食資訊，不是醫療建議。問題涉及疾病、懷孕、哺乳或特殊飲食需求時，提醒使用者諮詢醫師或營養師。
8. 使用繁體中文，語氣友善、簡潔。
9. 計算個人的熱量或份數需要性別、年齡和生活活動強度。使用者沒有提供時，請先詢問，不要自行假設。"""

AGENTS = {
    name: create_agent(model=model, tools=ALL_TOOLS, system_prompt=SYSTEM_PROMPT)
    for name, model in config.AGENT_MODELS.items()
}

def extract_trace(messages: list) -> list[dict]:
    """回傳 [{"tool": 工具名稱, "args": 參數, "output": 工具回傳的文字}, ...]"""
    steps = {}
    for m in messages:
        if getattr(m, "tool_calls", None):
            for call in m.tool_calls:
                steps[call["id"]] = {"tool": call["name"], "args": call["args"], "output": ""}
        elif type(m).__name__ == "ToolMessage" and m.tool_call_id in steps:
            steps[m.tool_call_id]["output"] = m.text
    return list(steps.values())

def friendly_error(e: Exception) -> str:
    """把錯誤訊息轉成使用者看得懂的說明"""
    msg = str(e)
    if "RESOURCE_EXHAUSTED" in msg or "429" in msg or "rate" in msg.lower():
        return "API 額度已用完或請求太頻繁，請稍後再試，或更換模型。"
    if "NOT_FOUND" in msg or "404" in msg:
        return "找不到這個模型，請到 config.py 確認模型名稱。"
    if "api_key" in msg.lower() or "401" in msg or "authentication" in msg.lower():
        return "API 金鑰有問題，請確認 .env 的設定。"
    return f"發生錯誤：{type(e).__name__}: {msg[:200]}"

def run_agent(name: str, question: str, history: list[dict] | None = None) -> dict:
    """
    history：這個模型之前的對話，格式 [{"role": "user"|"assistant", "content": "..."}]
    回傳：{"name", "answer", "trace", "seconds", "error"}
    """
    messages = (history or []) + [{"role": "user", "content": question}]
    start = time.time()
    try:
        result = AGENTS[name].invoke({"messages": messages})
        new_messages = result["messages"][len(messages):]
        return {
            "name": name,
            "answer": result["messages"][-1].text,
            "trace": extract_trace(new_messages),
            "seconds": time.time() - start,
            "error": None,
        }
    except Exception as e:
        return {
            "name": name, "answer": "", "trace": [],
            "seconds": time.time() - start, "error": friendly_error(e),
        }

def ask_all(question: str, histories: dict[str, list[dict]] | None = None) -> list[dict]:
    """histories：{"Claude": [...], "Gemini": [...]}，每個模型各自的對話紀錄"""
    histories = histories or {}
    with ThreadPoolExecutor(max_workers=len(AGENTS)) as pool:
        futures = [
            pool.submit(run_agent, name, question, histories.get(name))
            for name in AGENTS
        ]
        return [f.result() for f in futures]


if __name__ == "__main__":
    for r in ask_all("我是 29 歲男生，活動量適度，一天要吃幾份蛋白質？"):
        print(f"\n{'=' * 20} {r['name']}（{r['seconds']:.1f} 秒）{'=' * 20}")
        if r["error"]:
            print(r["error"])
            continue
        for step in r["trace"]:
            print(f"🔧 {step['tool']}({step['args']})")
        print(f"🤖 {r['answer']}")