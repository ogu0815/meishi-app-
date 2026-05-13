# =============================================================
# 名刺管理 Web アプリ
# 起動方法: python3 app.py
# ブラウザで http://localhost:5000 を開く
# =============================================================

import os
import re
import json
import uuid
import platform
from PIL import Image
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
import pandas as pd
from storage import append_record, read_all_records, delete_record, update_record, STORAGE_MODE

# HEIC対応
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

# -------------------------------------------------------------
# Google Vision API 初期化
# Render.com では環境変数 GOOGLE_CREDENTIALS_JSON に JSON 文字列を設定
# ローカルでは credentials.json ファイルを使用
# -------------------------------------------------------------
from google.cloud import vision
from google.oauth2.service_account import Credentials as SACredentials

def _get_vision_client():
    """Vision API クライアントを返す（環境変数 or ファイル）"""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        # Render.com：環境変数から認証情報を読み込む
        info = json.loads(creds_json)
        creds = SACredentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return vision.ImageAnnotatorClient(credentials=creds)
    else:
        # ローカル：credentials.json ファイルから読み込む
        creds = SACredentials.from_service_account_file(
            "credentials.json",
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return vision.ImageAnnotatorClient(credentials=creds)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "meishi-secret-key-change-in-production")

# パスワード設定（環境変数 APP_PASSWORD で変更可能）
APP_PASSWORD = os.environ.get("APP_PASSWORD", "meishi1234")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

UPLOAD_FOLDER  = "uploads"
SAVED_FOLDER   = "uploads/saved"   # 名刺画像の永続保存先
OUTPUT_CSV     = "meishi_data.csv"
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "heic", "heif", "tif", "tiff", "pdf"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(SAVED_FOLDER, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 最大16MB


# -------------------------------------------------------------
# OCR・情報抽出ロジック（meishi_reader.py と同じ）
# -------------------------------------------------------------

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_text(image_path):
    """Google Vision API で画像からテキストを抽出する"""
    client = _get_vision_client()
    with open(image_path, "rb") as f:
        content = f.read()
    image = vision.Image(content=content)
    response = client.text_detection(image=image)
    if response.error.message:
        raise RuntimeError(f"Vision API エラー: {response.error.message}")
    texts = response.text_annotations
    return texts[0].description if texts else ""


def extract_phone(text):
    matches = re.findall(r'(?:\+81[-\s]?)?0\d{1,4}[-\s]?\d{2,4}[-\s]?\d{3,4}', text)
    return matches[0].strip() if matches else ""


def extract_email(text):
    matches = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', text)
    return matches[0].strip() if matches else ""


def extract_name(text):
    # 役職キーワード（名前候補から除外する）
    TITLE_WORDS = [
        '代表取締役', '取締役', '専務', '常務', '監査役', '執行役員',
        '社長', '副社長', '会長', '副会長', '部長', '副部長', '課長',
        '係長', '主任', '担当', '本部長', '局長', '店長', '支店長',
        'マネージャー', 'ディレクター', 'リーダー',
        'CEO', 'COO', 'CFO', 'CTO', 'CMO', 'President', 'Director', 'Manager'
    ]
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines:
        m = re.search(r'(?:氏名|名前)[：:]\s*(.+)', line)
        if m:
            return m.group(1).strip()
    for line in lines:
        # 役職キーワードのみの行はスキップ
        if any(line == tw or line.strip() == tw for tw in TITLE_WORDS):
            continue
        n = line.replace('\u3000', ' ').strip()
        if re.fullmatch(r'[\u4e00-\u9fff]{2,4}\s[\u4e00-\u9fff]{1,4}', n):
            return n
        if re.fullmatch(r'[\u4e00-\u9fff]{2,6}', n):
            return n
    for i, line in enumerate(lines):
        if re.search(r'[\u30A0-\u30FF]{2,}', line) and i + 1 < len(lines):
            if re.search(r'[\u4e00-\u9fff]', lines[i + 1]):
                cand = lines[i + 1]
                if not any(cand == tw for tw in TITLE_WORDS):
                    return cand
    return ""


def extract_company(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    keywords = ['株式会社', '有限会社', '合同会社', '合名会社', '合資会社',
                '一般社団法人', '公益社団法人', '一般財団法人', 'Inc.', 'Co.,', 'Ltd.', 'LLC']
    for line in lines:
        for kw in keywords:
            if kw in line:
                return line.strip()
    return ""


def extract_department(text):
    """部署名を抽出する。「部」「課」「室」「グループ」「チーム」などを含む行を候補とする。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # ラベル付きパターン（「部署：営業部」など）
    for line in lines:
        m = re.search(r'(?:部署|所属)[：:]\s*(.+)', line)
        if m:
            return m.group(1).strip()

    # 部署名キーワードを含む行（会社名・住所・TEL行は除外）
    dept_keywords = ['部', '課', '室', 'グループ', 'チーム', 'Division', 'Department', 'Dept']
    exclude = ['株式会社', '有限会社', '合同会社', '法人', 'TEL', 'FAX', 'Email',
               '@', '〒', '都', '道', '府', '県']
    for line in lines:
        if any(ex in line for ex in exclude):
            continue
        for kw in dept_keywords:
            if kw in line and len(line) <= 30:
                return line.strip()
    return ""


def extract_title(text):
    """役職名を抽出する。代表的な役職キーワードを含む行を候補とする。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # ラベル付きパターン（「役職：部長」など）
    for line in lines:
        m = re.search(r'(?:役職|職位|肩書)[：:]\s*(.+)', line)
        if m:
            return m.group(1).strip()

    # 役職キーワードを含む行
    title_keywords = [
        '代表取締役', '取締役', '専務', '常務', '監査役', '執行役員',
        '社長', '副社長', '会長', '副会長',
        '部長', '副部長', '課長', '係長', '主任', '担当',
        '本部長', '局長', 'マネージャー', 'ディレクター', 'リーダー',
        'CEO', 'COO', 'CFO', 'CTO', 'CMO', 'President', 'Director', 'Manager'
    ]
    for line in lines:
        for kw in title_keywords:
            if kw in line and len(line) <= 25:
                return line.strip()
    return ""


def extract_address(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        # 電話番号っぽい行は住所から除外
        if re.search(r'TEL|FAX|tel|fax', line):
            continue
        if re.fullmatch(r'[\d\-\(\)\+\s]{7,}', line):
            continue
        if re.search(r'[〒〒7]?\s*\d{3}[-ー]\d{4}', line):
            part = re.sub(r'〒?\d{3}[-ー]\d{4}\s*', '', line).strip()
            if part:
                return line.strip()
            elif i + 1 < len(lines):
                return line.strip() + " " + lines[i + 1].strip()
    prefectures = ['北海道','青森','岩手','宮城','秋田','山形','福島','茨城','栃木','群馬',
                   '埼玉','千葉','東京','神奈川','新潟','富山','石川','福井','山梨','長野',
                   '岐阜','静岡','愛知','三重','滋賀','京都','大阪','兵庫','奈良','和歌山',
                   '鳥取','島根','岡山','広島','山口','徳島','香川','愛媛','高知','福岡',
                   '佐賀','長崎','熊本','大分','宮崎','鹿児島','沖縄']
    for line in lines:
        if re.search(r'TEL|FAX|tel|fax', line):
            continue
        if re.fullmatch(r'[\d\-\(\)\+\s]{7,}', line):
            continue
        for pref in prefectures:
            if pref in line:
                return line.strip()
    return ""


def parse_card(text, filename):
    return {
        "ファイル名": filename,
        "名前":       extract_name(text),
        "会社名":     extract_company(text),
        "部署":       extract_department(text),
        "役職":       extract_title(text),
        "電話番号":   extract_phone(text),
        "メール":     extract_email(text),
        "住所":       extract_address(text),
    }


# -------------------------------------------------------------
# 複数名刺の分割ロジック
# -------------------------------------------------------------

COMPANY_KEYWORDS = ['株式会社', '有限会社', '合同会社', '合名会社', '合資会社',
                    '一般社団法人', '公益社団法人', '一般財団法人', 'Inc.', 'Co.,', 'Ltd.', 'LLC']

def _is_card_boundary(line: str) -> bool:
    """この行が新しい名刺の区切りになりうるか判定する"""
    # 会社名キーワードを含む行
    for kw in COMPANY_KEYWORDS:
        if kw in line:
            return True
    # 氏名ラベル
    if re.search(r'(?:氏名|名前)[：:]', line):
        return True
    return False


def split_into_cards(text: str) -> list[str]:
    """
    OCRテキストを名刺ごとのブロックに分割する。

    分割アルゴリズム:
    1. 空白行2行以上 → 名刺の区切りとみなす
    2. 空白行が少ない場合 → 会社名キーワードが出現するたびに新しい名刺とみなす
    """
    # --- Step1: 空白行で大きく区切る ---
    blocks = re.split(r'\n{2,}', text.strip())
    blocks = [b.strip() for b in blocks if b.strip()]

    if len(blocks) >= 2:
        return blocks

    # --- Step2: 空白行がない場合、会社名キーワード行を区切りにする ---
    lines = [l.strip() for l in text.splitlines()]
    cards = []
    current = []

    for line in lines:
        if current and _is_card_boundary(line):
            # すでに何か溜まっていて、新しい会社名が来たら区切る
            cards.append("\n".join(current))
            current = [line]
        else:
            current.append(line)

    if current:
        cards.append("\n".join(current))

    return [c for c in cards if c.strip()] or [text]


def parse_multiple_cards(text: str, filename: str) -> list[dict]:
    """
    OCRテキストから複数名刺分のデータリストを返す。
    名刺が1枚しかない場合もリスト（1要素）で返す。
    """
    blocks = split_into_cards(text)
    results = []

    for i, block in enumerate(blocks):
        data = parse_card(block, filename)
        # 最低1項目以上抽出できたブロックのみ有効とする
        values = [data["名前"], data["会社名"], data["電話番号"], data["メール"], data["住所"]]
        if any(v for v in values):
            # 複数枚の場合はファイル名に番号を付ける
            if len(blocks) > 1:
                data["ファイル名"] = f"{filename} ({i+1}枚目)"
            results.append(data)

    return results if results else [parse_card(text, filename)]




# -------------------------------------------------------------
# ルーティング
# -------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "パスワードが違います"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    """トップページ"""
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    """画像をアップロードしてOCR処理する"""
    if "file" not in request.files:
        return jsonify({"error": "ファイルがありません"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "ファイルが選択されていません"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "対応形式は JPG / PNG / HEIC のみです"}), 400

    ext = file.filename.rsplit(".", 1)[1].lower()
    img_id = uuid.uuid4().hex

    # OCR用一時ファイル
    tmp_path  = os.path.join(UPLOAD_FOLDER, f"{img_id}.{ext}")
    # 永続保存（JPG変換して保存）
    saved_filename = f"{img_id}.jpg"
    saved_path = os.path.join(SAVED_FOLDER, saved_filename)

    file.save(tmp_path)

    try:
        all_cards = []

        if ext == "pdf":
            # PDF：各ページを画像に変換してOCR
            import fitz  # PyMuPDF
            pdf = fitz.open(tmp_path)
            for page_num in range(len(pdf)):
                page = pdf[page_num]
                mat = fitz.Matrix(2.0, 2.0)  # 解像度2倍
                pix = page.get_pixmap(matrix=mat)
                page_img_id = uuid.uuid4().hex
                page_saved_filename = f"{page_img_id}.jpg"
                page_saved_path = os.path.join(SAVED_FOLDER, page_saved_filename)
                pix.save(page_saved_path)

                text = extract_text(page_saved_path)
                page_label = f"{file.filename} (P{page_num + 1})"
                cards = parse_multiple_cards(text, page_label)
                for card in cards:
                    card["画像"] = page_saved_filename
                all_cards.extend(cards)
            pdf.close()
        else:
            # 画像ファイル（JPG / PNG / HEIC / TIFF）
            img = Image.open(tmp_path).convert("RGB")
            img.save(saved_path, "JPEG", quality=85)
            text = extract_text(saved_path)
            cards = parse_multiple_cards(text, file.filename)
            for card in cards:
                card["画像"] = saved_filename
            all_cards = cards

        return jsonify({"success": True, "cards": all_cards, "count": len(all_cards)})

    except Exception as e:
        if os.path.exists(saved_path):
            os.remove(saved_path)
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.route("/save", methods=["POST"])
@login_required
def save_card():
    """編集済みの名刺データを保存する"""
    data = request.json.get("data")
    if not data:
        return jsonify({"error": "データがありません"}), 400
    try:
        append_record(data)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/records")
@login_required
def records():
    """保存済みの名刺一覧を返す"""
    try:
        return jsonify(read_all_records())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/download")
@login_required
def download():
    """データをCSVとしてダウンロードする"""
    try:
        records_data = read_all_records()
        if not records_data:
            return "データがまだありません", 404
        df = pd.DataFrame(records_data)
        tmp_path = "meishi_download_tmp.csv"
        df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
        return send_file(tmp_path, as_attachment=True, download_name="meishi_data.csv")
    except Exception as e:
        return str(e), 500


@app.route("/image/<filename>")
@login_required
def serve_image(filename):
    """保存済み名刺画像を返す"""
    path = os.path.join(SAVED_FOLDER, filename)
    if not os.path.exists(path):
        return "", 404
    return send_file(path, mimetype="image/jpeg")


@app.route("/delete", methods=["POST"])
@login_required
def delete():
    """指定行を削除する"""
    index = request.json.get("index")
    try:
        delete_record(index)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/update", methods=["POST"])
@login_required
def update():
    """指定行を更新する"""
    payload = request.json
    index = payload.get("index")
    data  = payload.get("data", {})
    try:
        update_record(index, data)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    print("=" * 40)
    print(f"  名刺管理アプリ 起動中... (保存先: {STORAGE_MODE})")
    print("  ブラウザで以下を開いてください:")
    print("  http://localhost:8000")
    print("=" * 40)
    app.run(debug=True, host="0.0.0.0", port=8000)
