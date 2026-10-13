"""
營養飲食助理：多個模型同時回答，左右並排比較
執行：streamlit run app.py
"""
import streamlit as st

import agents

st.set_page_config(page_title="營養飲食助理", page_icon="🥗", layout="wide")
st.title("🥗 營養飲食助理")
st.caption(
    "資料來源：衛福部國民健康署《每日飲食指南手冊》、Open Food Facts。"
    "本服務提供一般飲食資訊，不能取代醫師或營養師的建議。"
)

MODEL_NAMES = list(agents.AGENTS)

if "turns" not in st.session_state:
    st.session_state.turns = []                                      # 每一輪的問題和各模型的結果
    st.session_state.histories = {name: [] for name in MODEL_NAMES}  # 每個模型各自的對話紀錄

with st.sidebar:
    st.header("設定")
    st.write("比較的模型：")
    for name, model in agents.config.AGENT_MODELS.items():
        st.write(f"- **{name}**：`{model}`")

    if st.button("清除對話", use_container_width=True):
        st.session_state.turns = []
        st.session_state.histories = {name: [] for name in MODEL_NAMES}
        st.rerun()

    st.divider()
    st.markdown("**可以試試：**")
    st.markdown(
        "- 一份蛋白質可以換成多少豆腐？\n"
        "- 我一天需要多少蛋白質？\n"
        "- Nutella 的熱量和糖分是多少？\n"
        "- 我有糖尿病，可以吃 Oreo 嗎？"
    )

def show_result(r: dict) -> None:
    st.subheader(r["name"])
    if r["error"]:
        st.error(r["error"])
        return

    st.caption(f"⏱️ {r['seconds']:.1f} 秒　🔧 {len(r['trace'])} 次工具呼叫")
    st.markdown(r["answer"])

    if r["trace"]:
        with st.expander("查看查詢路徑"):
            for i, step in enumerate(r["trace"], start=1):
                args = "、".join(f"{k}={v}" for k, v in step["args"].items())
                st.markdown(f"**{i}. `{step['tool']}`**　{args}")
                st.text(step["output"][:800])

for turn in st.session_state.turns:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    cols = st.columns(len(turn["results"]))
    for col, r in zip(cols, turn["results"]):
        with col:
            show_result(r)
    st.divider()

if question := st.chat_input("請輸入你的飲食問題"):
    with st.chat_message("user"):
        st.markdown(question)

    with st.spinner("模型查詢中…"):
        results = agents.ask_all(question, st.session_state.histories)

    # 更新每個模型的對話紀錄（只記錄成功回答的）
    for r in results:
        if not r["error"]:
            st.session_state.histories[r["name"]] += [
                {"role": "user", "content": question},
                {"role": "assistant", "content": r["answer"]},
            ]

    st.session_state.turns.append({"question": question, "results": results})
    st.rerun()