import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import json
from datetime import datetime

st.set_page_config(page_title="Kirana Store Agent", page_icon="🏪", layout="centered")

# ---------------- THEME / STYLING (Kirana store look) ----------------
st.markdown(
    """
<style>
.stApp {
    background: linear-gradient(180deg, #fdf6e3 0%, #f5ead0 100%);
}
[data-testid="stChatMessage"] {
    background-color: #fffdf8;
    border-radius: 14px;
    padding: 6px 10px;
    border: 1px solid #e6d5a8;
}
h1 {
    color: #7a4b1e !important;
    text-align: center;
}
.stChatInput {
    border-radius: 12px;
}
[data-testid="stSidebar"] {
    background-color: #f4e8c9;
}
</style>
""",
    unsafe_allow_html=True,
)

st.markdown("<h1>🏪 Kirana Store Agent 🌾🛢️🧂</h1>", unsafe_allow_html=True)
st.caption("Apna order likho, ya list ki photo/file bhejo — hum bill bana denge!")

# ---------------- CONFIG FROM SECRETS ----------------
try:
    gemini_key = st.secrets["GEMINI_API_KEY"]
    sheet_url = st.secrets["SHEET_URL"]
    creds_dict = json.loads(st.secrets["GCP_SERVICE_ACCOUNT_JSON"])
except Exception:
    st.error(
        "secrets.toml me GEMINI_API_KEY, SHEET_URL, aur GCP_SERVICE_ACCOUNT_JSON missing hai."
    )
    st.stop()


# ---------------- GOOGLE SHEETS CONNECTION ----------------
@st.cache_resource
def connect_sheet(creds_dict, sheet_url):
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(sheet_url)
    return sheet


try:
    sheet = connect_sheet(creds_dict, sheet_url)
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
- Customers may send text, or an image/PDF/file of a handwritten or printed grocery list.
  If a file is given, first read out the items you can identify from it, then treat it
  exactly like a text order.
- When a customer sends a product list (text or from a file), match products
  case-insensitively against the price list, convert quantities to the list's unit
  (e.g. 250 g vs per-kg price), and present a clear itemized bill with a total.
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
        if msg.get("file_name"):
            st.caption(f"📎 {msg['file_name']}")
        st.markdown(msg["content"])

with st.expander("📎 Photo ya File attach karo (optional)"):
    uploaded_file = st.file_uploader(
        "Box pe click karke Ctrl+V se bhi paste kar sakte ho",
        type=["jpg", "jpeg", "png", "pdf"],
        label_visibility="collapsed",
    )
user_input = st.chat_input("Order likho ya sawal poocho...")


def handle_message(text, uploaded_file=None):
    file_name = uploaded_file.name if uploaded_file else None
    st.session_state.messages.append(
        {"role": "user", "content": text or "(file bheji)", "file_name": file_name}
    )
    with st.chat_message("user"):
        if file_name:
            st.caption(f"📎 {file_name}")
        st.markdown(text or "(file bheji)")

    parts = []
    if uploaded_file is not None:
        parts.append(
            {"mime_type": uploaded_file.type, "data": uploaded_file.getvalue()}
        )
    parts.append(text or "Is file me jo grocery items hain unka bill banao.")

    response = st.session_state.chat.send_message(parts)
    reply_text = response.text

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

    st.session_state.messages.append(
        {"role": "assistant", "content": reply_text, "file_name": None}
    )
    with st.chat_message("assistant"):
        st.markdown(reply_text)


if user_input or uploaded_file:
    handle_message(user_input, uploaded_file)
