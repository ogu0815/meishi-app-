# =============================================================
# 名刺一括処理ツール
# 機能：指定フォルダ内の名刺画像をまとめてOCR処理し、CSVに保存する
# 使い方：images フォルダに名刺画像（jpg/png/heic）を入れて実行するだけ
# 対応形式：JPG / PNG / HEIC（iPhone写真）
# =============================================================

import os
import re
import glob
import pytesseract
from PIL import Image
import pandas as pd

# HEIC対応ライブラリを読み込む（インストール済みの場合のみ有効）
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()  # PillowでHEICが開けるようになる
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False

# -------------------------------------------------------------
# 設定：Tesseractのパス（OSを自動判定して設定）
# Windowsの場合は自動でパスを設定します。
# インストール先を変えた場合は WINDOWS_TESSERACT_PATH を書き換えてください。
# -------------------------------------------------------------
import platform

WINDOWS_TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = WINDOWS_TESSERACT_PATH

# 画像を格納するフォルダ名
IMAGE_FOLDER = "images"

# 出力CSVファイル名
OUTPUT_CSV = "meishi_data.csv"

# 対応する画像の拡張子（HEICを追加）
IMAGE_EXTENSIONS = [
    "*.jpg", "*.jpeg", "*.png",
    "*.JPG", "*.JPEG", "*.PNG",
    "*.heic", "*.HEIC", "*.heif", "*.HEIF",
]


# -------------------------------------------------------------
# OCR：画像からテキストを抽出する
# -------------------------------------------------------------
def extract_text_from_image(image_path: str) -> str:
    ext = os.path.splitext(image_path)[1].lower()

    # HEICファイルで pillow-heif が未導入の場合に案内する
    if ext in (".heic", ".heif") and not HEIC_SUPPORTED:
        raise RuntimeError(
            "HEICファイルを処理するには pillow-heif が必要です。\n"
            "  pip3 install pillow-heif を実行してください。"
        )

    image = Image.open(image_path).convert("RGB")
    text = pytesseract.image_to_string(image, lang="jpn+eng")
    return text


# -------------------------------------------------------------
# 各情報の抽出関数
# -------------------------------------------------------------

def extract_phone(text: str) -> str:
    pattern = r'(?:\+81[-\s]?)?0\d{1,4}[-\s]?\d{2,4}[-\s]?\d{3,4}'
    matches = re.findall(pattern, text)
    return matches[0].strip() if matches else ""


def extract_email(text: str) -> str:
    pattern = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    matches = re.findall(pattern, text)
    return matches[0].strip() if matches else ""


def extract_name(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for line in lines:
        m = re.search(r'(?:氏名|名前)[：:]\s*(.+)', line)
        if m:
            return m.group(1).strip()

    for line in lines:
        normalized = line.replace('\u3000', ' ').strip()
        if re.fullmatch(r'[\u4e00-\u9fff]{2,4}\s[\u4e00-\u9fff]{1,4}', normalized):
            return normalized
        if re.fullmatch(r'[\u4e00-\u9fff]{2,6}', normalized):
            return normalized

    for i, line in enumerate(lines):
        if re.search(r'[\u30A0-\u30FF]{2,}', line):
            if i + 1 < len(lines):
                candidate = lines[i + 1]
                if re.search(r'[\u4e00-\u9fff]', candidate):
                    return candidate

    return ""


def extract_company(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    keywords = [
        '株式会社', '有限会社', '合同会社', '合名会社', '合資会社',
        '一般社団法人', '公益社団法人', '一般財団法人', 'Inc.', 'Co.,', 'Ltd.', 'LLC'
    ]
    for line in lines:
        for kw in keywords:
            if kw in line:
                return line.strip()
    return ""


def extract_address(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for i, line in enumerate(lines):
        if re.search(r'[〒〒7]?\s*\d{3}[-ー]\d{4}', line) and not re.search(r'TEL|FAX|tel|fax', line):
            address_part = re.sub(r'〒?\d{3}[-ー]\d{4}\s*', '', line).strip()
            if address_part:
                return line.strip()
            elif i + 1 < len(lines):
                return line.strip() + " " + lines[i + 1].strip()

    prefectures = [
        '北海道', '青森', '岩手', '宮城', '秋田', '山形', '福島',
        '茨城', '栃木', '群馬', '埼玉', '千葉', '東京', '神奈川',
        '新潟', '富山', '石川', '福井', '山梨', '長野', '岐阜',
        '静岡', '愛知', '三重', '滋賀', '京都', '大阪', '兵庫',
        '奈良', '和歌山', '鳥取', '島根', '岡山', '広島', '山口',
        '徳島', '香川', '愛媛', '高知', '福岡', '佐賀', '長崎',
        '熊本', '大分', '宮崎', '鹿児島', '沖縄'
    ]
    for line in lines:
        if re.search(r'TEL|FAX|tel|fax', line):
            continue
        for pref in prefectures:
            if pref in line:
                return line.strip()

    return ""


def parse_meishi(text: str, filename: str) -> dict:
    return {
        "ファイル名": filename,
        "名前":       extract_name(text),
        "会社名":     extract_company(text),
        "電話番号":   extract_phone(text),
        "メール":     extract_email(text),
        "住所":       extract_address(text),
    }


# -------------------------------------------------------------
# 一括処理メイン
# -------------------------------------------------------------
def main():
    # imagesフォルダがなければ作成して案内する
    if not os.path.exists(IMAGE_FOLDER):
        os.makedirs(IMAGE_FOLDER)
        print(f"[案内] 「{IMAGE_FOLDER}」フォルダを作成しました。")
        print(f"       名刺画像（jpg / png）をこのフォルダに入れて再実行してください。")
        return

    # 対応拡張子の画像を全て収集
    image_files = []
    for ext in IMAGE_EXTENSIONS:
        image_files.extend(glob.glob(os.path.join(IMAGE_FOLDER, ext)))

    # 重複除去してソート
    image_files = sorted(set(image_files))

    if not image_files:
        print(f"[案内] 「{IMAGE_FOLDER}」フォルダに画像が見つかりませんでした。")
        print(f"       jpg または png ファイルを入れて再実行してください。")
        return

    print(f"[開始] {len(image_files)} 枚の名刺を処理します。\n")
    print("-" * 50)

    results = []
    success = 0
    failed = 0

    for i, image_path in enumerate(image_files, 1):
        filename = os.path.basename(image_path)
        print(f"[{i}/{len(image_files)}] 処理中: {filename}")

        try:
            # OCRでテキスト抽出
            text = extract_text_from_image(image_path)

            # 情報を解析
            data = parse_meishi(text, filename)
            results.append(data)

            # 抽出結果をその場で表示
            print(f"  名前    : {data['名前'] or '（未検出）'}")
            print(f"  会社名  : {data['会社名'] or '（未検出）'}")
            print(f"  電話番号: {data['電話番号'] or '（未検出）'}")
            print(f"  メール  : {data['メール'] or '（未検出）'}")
            print(f"  住所    : {data['住所'] or '（未検出）'}")
            success += 1

        except Exception as e:
            print(f"  [エラー] 処理失敗: {e}")
            # エラーが出た画像も記録（空データとして残す）
            results.append({
                "ファイル名": filename,
                "名前": "", "会社名": "", "電話番号": "", "メール": "", "住所": "",
            })
            failed += 1

        print("-" * 50)

    # CSV保存
    if results:
        df = pd.DataFrame(results)
        df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
        print(f"\n[完了] CSVに保存しました: {OUTPUT_CSV}")

    # 処理サマリー
    print(f"\n{'='*50}")
    print(f"  処理完了   : {len(image_files)} 枚")
    print(f"  成功       : {success} 枚")
    print(f"  エラー     : {failed} 枚")
    print(f"  出力ファイル: {OUTPUT_CSV}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
