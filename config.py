from pathlib import Path

PDF_PATH = Path("data/daily_food_guide.pdf")
PAGE_DIR =  Path("data/pages")
SKIP_PAGES = {1,3,29}
SOURCE_NAME = "每日飲食指南手冊"

DB_DIR = "./db"
COLLECTION = "food_guide"
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"

VISION_MODEL = "anthropic:claude-sonnet-5-5"

# ===== Open Food Facts =====
OFF_USER_AGENT = "NutritionAssistant/0.1 (anthonylin901@gmail.com)"

# ===== Agent 使用的模型（名稱會更新，請以各家官方文件為準）=====
AGENT_MODELS = {
    "Claude": "anthropic:claude-sonnet-5-5",
    "Gemini": "google_genai:gemini-3.5-flash-lite",
}