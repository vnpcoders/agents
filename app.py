import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import json
import re
from datetime import datetime

st.set_page_config(page_title="Kirana Store Agent", page_icon="🏪", layout="centered")

# ---------------- THEME / STYLING (Blue Glass look) ----------------
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

.stApp {
    background: linear-gradient(160deg, #dcebfb 0%, #c3dcf7 55%, #eaf2fc 100%);
    background-attachment: fixed;
}

/* soft glow blobs behind everything so the glass blur has something to catch */
.stApp::before, .stApp::after {
    content: "";
    position: fixed;
    border-radius: 50%;
    filter: blur(70px);
    z-index: 0;
    pointer-events: none;
}
.stApp::before {
    width: 380px; height: 380px;
    background: radial-gradient(circle, #2f5fd6 0%, transparent 70%);
    top: -80px; left: -100px;
    opacity: 0.30;
}
.stApp::after {
    width: 420px; height: 420px;
    background: radial-gradient(circle, #48c0d9 0%, transparent 70%);
    bottom: -120px; right: -120px;
    opacity: 0.28;
}

h1 {
    font-family: 'Space Grotesk', sans-serif !important;
    font-weight: 700 !important;
    color: #16296b !important;
    text-align: center;
    letter-spacing: -0.3px;
}

/* glass chat bubbles */
[data-testid="stChatMessage"] {
    background: rgba(255, 255, 255, 0.55) !important;
    backdrop-filter: blur(14px) saturate(160%);
    -webkit-backdrop-filter: blur(14px) saturate(160%);
    border: 1px solid rgba(255, 255, 255, 0.65) !important;
    border-radius: 16px !important;
    padding: 10px 14px !important;
    box-shadow: 0 10px 24px -14px rgba(22, 41, 107, 0.30);
}

/* user messages get a blue-tinted glass */
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
    background: rgba(47, 95, 214, 0.14) !important;
    border: 1px solid rgba(47, 95, 214, 0.30) !important;
}

/* glass input bar */
.stChatInput, .stChatInput > div {
    background: transparent !important;
}
.stChatInput textarea {
    background: rgba(255, 255, 255, 0.55) !important;
    backdrop-filter: blur(10px);
    border-radius: 14px !important;
    border: 1px solid rgba(255, 255, 255, 0.65) !important;
    font-family: 'Inter', sans-serif !important;
}
.stChatInput button {
    background: linear-gradient(135deg, #2f5fd6, #48c0d9) !important;
    border-radius: 10px !important;
}
.stChatInput button svg {
    fill: #ffffff !important;
}

/* sidebar / expander (file attach box) */
[data-testid="stSidebar"] {
    background: rgba(220, 235, 251, 0.55) !important;
    backdrop-filter: blur(10px);
}
.streamlit-expanderHeader {
    background: rgba(255, 255, 255, 0.45) !important;
    border-radius: 10px !important;
    border: 1px dashed rgba(47, 95, 214, 0.35) !important;
    font-family: 'Inter', sans-serif !important;
}
.streamlit-expanderContent {
    background: rgba(255, 255, 255, 0.30) !important;
    border-radius: 0 0 10px 10px !important;
}

/* captions feel like data -> monospace */
.stCaption, [data-testid="stCaptionContainer"] {
    font-family: 'IBM Plex Mono', monospace !important;
    color: #5b6b85 !important;
}

/* buttons generally (file uploader browse button etc.) */
button[kind="secondary"], .stButton button {
    background: linear-gradient(135deg, #2f5fd6, #48c0d9) !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 10px !important;
}

/* keep content above the glow blobs */
.block-container {
    position: relative;
    z-index: 1;
}
</style>
""",
    unsafe_allow_html=True,
)

st.markdown("<h1>🏪 Sargam — Kirana Store Agent</h1>", unsafe_allow_html=True)
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


def validate_customer_details(order):
    """Returns a list of human-readable problems (empty list = all good)."""
    errors = []

    name = str(order.get("customer_name", "")).strip()
    mobile = str(order.get("mobile_number", "")).strip()
    address = str(order.get("address", "")).strip()

    # Name: only letters (English or Hindi) and spaces/dots, no digits/symbols.
    if not re.fullmatch(r"[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F .]{1,49}", name):
        errors.append(
            f"Name '{name}' invalid hai — sirf letters aur spaces hone chahiye, "
            "koi number ya symbol nahi."
        )

    # Mobile: exactly 10 digits, Indian mobile numbers start with 6-9, digits only.
    if not re.fullmatch(r"[6-9]\d{9}", mobile):
        errors.append(
            f"Mobile number '{mobile}' invalid hai — exactly 10 digits ka hona "
            "chahiye, 6/7/8/9 se start ho, aur sirf digits (koi letter/symbol/+91 nahi)."
        )

    # Address: should look like a real address, not just symbols or a couple of chars.
    letter_count = len(re.findall(r"[A-Za-z\u0900-\u097F]", address))
    if len(address) < 8 or letter_count < 4:
        errors.append(
            f"Address '{address}' invalid hai — thoda detailed address likhein "
            "(house/street/area), sirf number ya symbol na ho."
        )

    return errors


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
You are Sargam a friendly grocery shop assistant. Currency is Indian Rupees (Rs).

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

BILL FORMATTING (important):
- Always present the itemized bill as a markdown table with columns "Item", "Qty",
  "Price", followed by a bold Total row. Do NOT use inline formats like
  "1 x Cooking Oil (Sunflower) (Rs 150)".
  Example:
  | Item | Qty | Price |
  |---|---|---|
  | Cooking Oil (Sunflower) | 1 | Rs 150 |
  | Maggi Noodles | 1 | Rs 14 |
  | **Total** | | **Rs 164** |

CUSTOMER DETAILS (important):
- After the customer confirms the bill (e.g. "ok place the order"), collect their name,
  mobile number, and address ONE AT A TIME (do not ask all three together).
- Each detail must be genuinely valid before you accept it and move to the next field.
  If a reply looks wrong, politely point out the issue and ask again for that same field
  ONLY (do not restart from name):
  - Name: only alphabets and spaces (Hindi or English), no digits, no symbols.
  - Mobile number: exactly 10 digits, only digits, starting with 6, 7, 8 or 9
    (standard Indian mobile format). No letters, no +91, no spaces, no symbols.
  - Address: a real-sounding address with at least a house/flat, street/area, and
    locality — not just a few random letters, digits, or symbols.
- Once all three are collected AND valid, reply with EXACTLY one line in this format so
  the app can save it, followed by a friendly confirmation message. The "items" field
  must use the SAME clean line-per-item format as the bill table (one item per line,
  "Item - Qty - Price"), ending with a Total line:
  ORDER_JSON: {{"customer_name": "...", "mobile_number": "...", "address": "...", "items": "Cooking Oil (Sunflower) - 1 - Rs 150\\nMaggi Noodles - 1 - Rs 14\\nTotal - Rs 164", "total_amount": <number>}}
- The app double-checks these details on its side too. If it flags an issue, you will
  get a SYSTEM_NOTE listing exactly what's wrong — ask the customer again for only
  that field, don't re-send ORDER_JSON until it's fixed.
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

            errors = validate_customer_details(order)
            if errors:
                # Don't save. Feed the exact issues back to Gemini so it asks the
                # customer again for only the field(s) that are wrong.
                correction_note = (
                    "SYSTEM_NOTE: Order was NOT saved because these customer details "
                    "failed validation:\n- "
                    + "\n- ".join(errors)
                    + "\nAsk the customer again for only the invalid field(s), in "
                    "Hinglish, and do not send ORDER_JSON again until all fields are valid."
                )
                correction_response = st.session_state.chat.send_message(correction_note)
                reply_text = correction_response.text
            else:
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
