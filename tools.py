"""
Agent 可以使用的工具
- search_guide        搜尋飲食指南手冊（RAG）
- get_daily_servings  依熱量查六大類建議份數（表四）
- get_calorie_needs   依性別、年齡、活動強度查熱量需求（表三）
- get_food            查詢市售食品營養成分（Open Food Facts API）
"""
from functools import lru_cache

import requests
from langchain.tools import tool

import config
import tables
from build_index import get_vector_store

vector_store = get_vector_store()

@tool(parse_docstring=True)
def search_guide(query: str) -> str:
    """搜尋衛福部國民健康署《每日飲食指南手冊》的內容。

        適合查詢飲食原則、六大類食物的營養成分與功能、份量代換方式
        （例如一份蛋白質等於多少豆腐）、各類食物的選擇建議等。
        如果問題提到具體熱量，要查各類建議份數，請改用 get_daily_servings。

        Args:
            query: 要搜尋的問題或關鍵字，例如「高鈣豆製品有哪些」
        """

    results = vector_store.similarity_search(query, k=4)
    parts = []
    for d in results:
        start = d.metadata["page"]
        end = d.metadata.get("page_end", start)
        if start == end:
            pages = f"第 {start} 頁"
        else:
            pages = f"第 {start}～{end} 頁"
        parts.append(f"[{pages}]\n{d.page_content}")
    return "\n\n".join(parts)


@tool(parse_docstring=True)
def get_daily_servings(kcal: int) -> str:
    """依每日熱量需求，精確查詢六大類飲食建議份數（每日飲食指南手冊表四）。

    當使用者提到具體的每日熱量（大卡），想知道各類食物要吃多少時使用。
    手冊只提供 1200、1500、1800、2000、2200、2500、2700 大卡七個等級，
    其他數值會自動對應到最接近的等級。

    Args:
        kcal: 每日熱量需求，單位為大卡，例如 1800
    """
    level = min(tables.KCALS, key=lambda k: abs(k - kcal))
    note = "" if level == kcal else f"（手冊沒有 {kcal} 大卡，以最接近的 {level} 大卡為準）"
    items = "\n".join(f"- {item}" for item in tables.servings_items(level))
    return (
        f"每日熱量 {level} 大卡的六大類飲食建議份數{note}：\n{items}\n"
        f"（出處：{config.SOURCE_NAME} 第 {tables.TABLE4_PAGE} 頁 表四）"
    )


@tool(parse_docstring=True)
def get_calorie_needs(sex: str, age: int, activity: str) -> str:
    """依性別、年齡和生活活動強度，查詢每日熱量需求（每日飲食指南手冊表三）。

    當使用者想知道自己一天需要多少熱量，或要計算個人的六大類建議份數時使用。
    查到熱量後，可以再用 get_daily_servings 查出各類食物的建議份數。
    三個參數都必須是使用者提供的資訊，不可以自行假設；使用者沒提供時，請先詢問。
    手冊只涵蓋 19 歲以上的成人。

    Args:
        sex: 性別，「男」或「女」
        age: 年齡（歲），19 歲以上
        activity: 生活活動強度，「低」、「稍低」、「適度」或「高」其中之一
    """
    if sex not in tables.TABLE3:
        return "性別請填「男」或「女」。"
    if activity not in tables.ACTIVITY_LEVELS:
        return f"生活活動強度請填 {tables.ACTIVITY_LEVELS} 其中之一。不確定的話，可以用 search_guide 查詢手冊表二的說明。"
    if age < 19:
        return "手冊的表三只涵蓋 19 歲以上的成人，無法查詢未成年人的熱量需求，建議諮詢醫師或營養師。"

    for (low, high), values in tables.TABLE3[sex].items():
        if low <= age <= high:
            kcal = values[tables.ACTIVITY_LEVELS.index(activity)]
            age_range = f"{low}-{high}" if high < 200 else f"{low}+"
            if kcal is None:
                return f"手冊表三沒有提供{sex}性 {age_range} 歲「{activity}」活動強度的熱量需求。"
            return (
                f"{sex}性、{age_range} 歲、生活活動強度「{activity}」的每日熱量需求為 {kcal} 大卡。\n"
                f"（注意：手冊的熱量是以 BMI 22 的參考體重計算，個人實際需求可能不同。）\n"
                f"（出處：{config.SOURCE_NAME} 第 {tables.TABLE3_PAGE} 頁 表三）"
            )



OFF_PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
OFF_FIELDS = "code,product_name,brands,quantity,nutriments"
HEADERS = {"User-Agent": config.OFF_USER_AGENT}

# API 欄位 → (中文名稱, 單位, 換算倍數)
NUTRIENTS = {
    "energy-kcal_100g":   ("熱量", "大卡", 1),
    "proteins_100g":      ("蛋白質", "公克", 1),
    "fat_100g":           ("脂肪", "公克", 1),
    "saturated-fat_100g": ("飽和脂肪", "公克", 1),
    "carbohydrates_100g": ("碳水化合物", "公克", 1),
    "sugars_100g":        ("糖", "公克", 1),
    "fiber_100g":         ("膳食纖維", "公克", 1),
    "sodium_100g":        ("鈉", "毫克", 1000),     # 公克 → 毫克
}


class OFFError(Exception):
    """Open Food Facts 暫時無法使用（不是查無資料）"""

def _request(url: str, params: dict, timeout: int) -> requests.Response | None:
    """送出請求，失敗時重試一次；成功回傳 Response，查無資料（404）回傳 None，其他錯誤拋出 OFFError"""
    last_error = ""
    for _ in range(2):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        except requests.Timeout:
            last_error = "連線逾時"
            continue
        except requests.RequestException as e:
            last_error = f"連線錯誤（{type(e).__name__}）"
            continue

        if r.status_code == 200:
            return r
        if r.status_code == 404:
            return None
        if r.status_code == 429:
            raise OFFError("請求太頻繁，暫時被限制")
        last_error = f"伺服器回應狀態碼 {r.status_code}"

    print(f"[Open Food Facts] 請求失敗：{last_error}")
    raise OFFError(last_error)

def clean_product(product: dict) -> dict:
    """把 API 回傳的產品資料，整理成需要的格式"""
    raw = product.get("nutriments", {})
    nutrition = {}
    for key, (name, unit, factor) in NUTRIENTS.items():
        if raw.get(key) is not None:
            nutrition[name] = f"{round(raw[key] * factor, 1)} {unit}"
    return {
        "條碼": product.get("code") or "（未提供）",
        "名稱": product.get("product_name") or "（未提供）",
        "品牌": product.get("brands") or "（未提供）",
        "容量": product.get("quantity") or "（未提供）",
        "每 100 公克營養成分": nutrition,
    }


@lru_cache(maxsize=256)
def fetch_product(barcode: str) -> dict | None:
    """用條碼查詢產品；查無資料回傳 None，服務出錯拋出 OFFError"""
    r = _request(OFF_PRODUCT_URL.format(barcode=barcode), {"fields": OFF_FIELDS}, timeout=10)
    if r is None:
        return None
    product = r.json().get("product")
    return clean_product(product) if product else None


@lru_cache(maxsize=256)
def search_products(name: str, limit: int = 3) -> list[dict]:
    """用名稱搜尋產品；查無資料回傳空 list，服務出錯拋出 OFFError"""
    params = {
        "search_terms": name, "search_simple": 1, "action": "process",
        "json": 1, "page_size": limit, "fields": OFF_FIELDS,
    }
    r = _request(OFF_SEARCH_URL, params, timeout=30)
    if r is None:
        return []
    return [clean_product(p) for p in r.json().get("products", [])]

@tool(parse_docstring=True)
def get_food(query: str) -> str:
    """查詢市售包裝食品的營養成分（資料來源：Open Food Facts 開放食品資料庫）。

    可以輸入條碼（8～14 位數字）或產品名稱。回傳的營養成分都是「每 100 公克」的數值。
    這個資料庫以國際產品為主，台灣產品的資料可能不齊全或查不到；
    查不到時請如實告知使用者，並請使用者提供包裝上的營養標示，不可以自行推測數值。

    Args:
        query: 產品條碼（8～14 位數字）或產品名稱（例如品牌加產品名）
    """
    try:
        if query.isdigit() and 8 <= len(query) <= 14:
            product = fetch_product(query)
            products = [product] if product else []
        else:
            products = search_products(query.strip().lower())
    except OFFError as e:
        return (
            f"Open Food Facts 暫時無法使用（{e}），這不代表沒有「{query}」這個產品。"
            f"請告知使用者服務暫時無法連線、請稍後再試，或請使用者提供包裝上的營養標示。"
        )
    except Exception as e:
        print(f"[get_food] 未預期的錯誤：{type(e).__name__}: {e}")
        return (
            f"查詢「{query}」時發生未預期的錯誤，這不代表沒有這個產品。"
            f"請告知使用者暫時無法查詢，或請使用者提供包裝上的營養標示。"
        )

    if not products:
        return f"Open Food Facts 資料庫中沒有「{query}」的資料。請建議使用者提供包裝上的營養標示。"

    parts = []
    for p in products:
        nutrition = "、".join(f"{k} {v}" for k, v in p["每 100 公克營養成分"].items())
        parts.append(
            f"- {p['名稱']}（品牌：{p['品牌']}，容量：{p['容量']}，條碼：{p['條碼']}）\n"
            f"  每 100 公克：{nutrition or '沒有營養資料'}"
        )
    return "Open Food Facts 查詢結果：\n" + "\n".join(parts)

ALL_TOOLS = [search_guide, get_daily_servings, get_calorie_needs, get_food]

if __name__ == "__main__":
    print(get_daily_servings.invoke({"kcal": 1900}))
    print()
    print(get_calorie_needs.invoke({"sex": "男", "age": 29, "activity": "適度"}))
    print()
    print(search_guide.invoke({"query": "什麼是高鈣豆製品"})[:300])
    print()
    print(get_food.invoke({"query": "3017624010701"}))