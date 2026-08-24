import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import json
from datetime import datetime

st.set_page_config(page_title="Grocery Shop Agent", page_icon="🛒")
st.title("🛒 Grocery Shop Agent")

# ---------------- SIDEBAR: CONFIG ----------------
st.sidebar.header("Setup")
gemini_key = st.sidebar.text_input("Gemini API Key", type="password")
sheet_url = st.sidebar.text_input("Google Sheet URL")
sa_json_text = st.sidebar.text_area(
    "Service Account JSON (paste full content)", height=100
)

if not (gemini_key and sheet_url and sa_json_text):
    st.info(
        "Sidebar me Gemini key, Sheet URL, aur Service Account JSON daal kar shuru karo."
    )
    st.stop()


# ---------------- GOOGLE SHEETS CONNECTION ----------------
@st.cache_resource
def connect_sheet(sa_json_text, sheet_url):
    creds_dict = json.loads(sa_json_text)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(sheet_url)
    return sheet


try:
    sheet = connect_sheet(sa_json_text, sheet_url)
    all_ws = {ws.title.strip().lower(): ws for ws in sheet.worksheets()}

    def find_ws(name):
        key = name.strip().lower()
        if key in all_ws:
            return all_ws[key]
        raise Exception(
            f"'{name}' tab nahi mili. Available tabs: {list(all_ws.keys())}"
        )

    products_ws = find_ws("Product Price List")
    qna_ws = find_ws("Question Answer")
    orders_ws = find_ws("order list")
except Exception as e:
    st.error(f"Sheet connect nahi hui: {e}")
    st.stop()


@st.cache_data(ttl=60)
def load_products():
    return products_ws.get_all_records()


@st.cache_data(ttl=60)
def load_qna():
    return qna_ws.get_all_records()


products = load_products()
qna = load_qna()


def save_order(customer_name, mobile_number, address, items, total_amount):
    orders_ws.append_row(
        [
            customer_name,
            mobile_number,
            address,
            items,
            total_amount,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ]
    )


# ---------------- GEMINI SETUP ----------------
genai.configure(api_key=gemini_key)

SYSTEM_INSTRUCTIONS = f"""
You are a friendly grocery shop assistant. Currency is Indian Rupees (Rs).

PRODUCT PRICE LIST (JSON):
{json.dumps(products, ensure_ascii=False)}

QUESTION-ANSWER KNOWLEDGE (JSON):
{json.dumps(qna, ensure_ascii=False)}

Core behavior:
- When a customer sends a product list, match products case-insensitively against the
  price list, convert quantities to the list's unit (e.g. 250 g vs per-kg price), and
  present a clear itemized bill with a total.
- If a product is not found in the price list, mention it as "not available" in the bill;
  never invent a price or silently drop it.
- After the customer confirms the bill (e.g. "ok place the order"), collect their name,
  mobile number, and address ONE AT A TIME (do not ask all three together).
- Once all three are collected, reply with EXACTLY one line in this format so the app can
  save it, followed by a friendly confirmation message:
  ORDER_JSON: {{"customer_name": "...", "mobile_number": "...", "address": "...", "items": "readable summary with prices and total", "total_amount": <number>}}
- For general shop questions, answer using the QUESTION-ANSWER KNOWLEDGE above.
- Be concise and clear, especially in bills and totals.
"""

model = genai.GenerativeModel(
    "gemini-3.6-flash", system_instruction=SYSTEM_INSTRUCTIONS
)

# ---------------- CHAT STATE ----------------
if "chat" not in st.session_state:
    st.session_state.chat = model.start_chat(history=[])
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

user_input = st.chat_input("Order likho ya sawal poocho...")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    response = st.session_state.chat.send_message(user_input)
    reply_text = response.text

    # Check if model produced an order to save
    if "ORDER_JSON:" in reply_text:
        try:
            json_part = reply_text.split("ORDER_JSON:", 1)[1].strip()
            json_str = json_part.split("\n", 1)[0].strip()
            order = json.loads(json_str)
            save_order(
                order["customer_name"],
                order["mobile_number"],
                order["address"],
                order["items"],
                order["total_amount"],
            )
            display_text = reply_text.split("ORDER_JSON:", 1)[0].strip()
            if not display_text:
                display_text = "✅ Aapka order save ho gaya hai! Dhanyavaad."
            reply_text = display_text
        except Exception as e:
            reply_text += f"\n\n(⚠️ Order save karne me issue: {e})"

    st.session_state.messages.append({"role": "assistant", "content": reply_text})
    with st.chat_message("assistant"):
        st.markdown(reply_text)
