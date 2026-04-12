# =============================================================
# 名刺情報抽出ツール
# 機能：名刺画像からOCRで文字を読み取り、CSVに保存する
# 使用ライブラリ：pytesseract, Pillow, re, pandas
# =============================================================

import re
import sys
import platform
import pytesseract
from PIL import Image
import pandas as pd


# -------------------------------------------------------------
# 設定：Tesseractのパス（OSを自動判定して設定）
# Windowsの場合は自動でパスを設定します。
# インストール先を変えた場合は WINDOWS_TESSERACT_PATH を書き換えてください。
# -------------------------------------------------------------
WINDOWS_TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = WINDOWS_TESSERACT_PATH


# -------------------------------------------------------------
# Step 1: 画像を読み込んでOCRでテキストを抽出する
# -------------------------------------------------------------
def extract_text_from_image(image_path: str) -> str:
    """
    画像ファイルを読み込み、日本語OCRでテキストを抽出する。

    Args:
        image_path: 画像ファイルのパス（例: "meishi.jpg"）

    Returns:
        抽出されたテキスト文字列
    """
    try:
        # 画像を開く
        image = Image.open(image_path)

        # 画像をRGBに変換（PNGなどのアルファチャンネルがある場合の対策）
        image = image.convert("RGB")

        # OCR実行（日本語 + 英語を指定）
        # lang="jpn+eng" にすると日本語・英語の両方を認識する
        text = pytesseract.image_to_string(image, lang="jpn+eng")

        print("=== OCR 抽出テキスト ===")
        print(text)
        print("========================\n")

        return text

    except FileNotFoundError:
        print(f"[エラー] 画像ファイルが見つかりません: {image_path}")
        sys.exit(1)
    except Exception as e:
        print(f"[エラー] OCR処理中に問題が発生しました: {e}")
        sys.exit(1)


# -------------------------------------------------------------
# Step 2: テキストから各種情報を正規表現で抽出する
# -------------------------------------------------------------

def extract_phone(text: str) -> str:
    """
    電話番号を抽出する。
    対応形式例: 03-1234-5678 / 090-1234-5678 / 0312345678 / +81-3-1234-5678
    """
    # 国際番号(+81...)、固定電話(0X-XXXX-XXXX)、携帯(0X0-XXXX-XXXX)に対応
    pattern = r'(?:\+81[-\s]?)?0\d{1,4}[-\s]?\d{2,4}[-\s]?\d{3,4}'
    matches = re.findall(pattern, text)
    return matches[0].strip() if matches else ""


def extract_email(text: str) -> str:
    """
    メールアドレスを抽出する。
    """
    pattern = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    matches = re.findall(pattern, text)
    return matches[0].strip() if matches else ""


def extract_name(text: str) -> str:
    """
    名前を推定抽出する。
    日本語名刺では「氏名」「名前」の後に続くテキスト、
    または単独行に2〜4文字の漢字が続くパターンを候補とする。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # ラベル付きパターン（「氏名：山田太郎」など）
    for line in lines:
        m = re.search(r'(?:氏名|名前)[：:]\s*(.+)', line)
        if m:
            return m.group(1).strip()

    # 2〜6文字の漢字（スペース区切りの姓名も許容）を名前候補とする
    for line in lines:
        # 「山田 太郎」のようにスペース区切りの漢字2〜6文字にも対応
        normalized = line.replace('\u3000', ' ').strip()
        if re.fullmatch(r'[\u4e00-\u9fff]{2,4}\s[\u4e00-\u9fff]{1,4}', normalized):
            return normalized
        if re.fullmatch(r'[\u4e00-\u9fff]{2,6}', normalized):
            return normalized

    # フリガナの直後の行を名前とみなすパターン
    for i, line in enumerate(lines):
        if re.search(r'[\u30A0-\u30FF]{2,}', line):  # カタカナ行
            if i + 1 < len(lines):
                candidate = lines[i + 1]
                if re.search(r'[\u4e00-\u9fff]', candidate):
                    return candidate

    return ""


def extract_company(text: str) -> str:
    """
    会社名を抽出する。
    「株式会社」「有限会社」「合同会社」などを含む行を候補とする。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # 会社形態キーワードが含まれる行を探す
    company_keywords = [
        '株式会社', '有限会社', '合同会社', '合名会社', '合資会社',
        '一般社団法人', '公益社団法人', '一般財団法人', 'Inc.', 'Co.,', 'Ltd.', 'LLC'
    ]
    for line in lines:
        for kw in company_keywords:
            if kw in line:
                return line.strip()

    return ""


def extract_address(text: str) -> str:
    """
    住所を抽出する。
    都道府県名や「〒」から始まる郵便番号を手がかりにする。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # 郵便番号から始まる行またはその次の行を住所とみなす
    # OCRで「〒」が「7」「〒」「〒」などに誤認識されることを考慮し複数パターンに対応
    for i, line in enumerate(lines):
        if re.search(r'[〒〒7]?\s*\d{3}[-ー]\d{4}', line) and not re.search(r'TEL|FAX|tel|fax', line):
            # 郵便番号と住所が同じ行にある場合
            address_part = re.sub(r'〒?\d{3}[-ー]\d{4}\s*', '', line).strip()
            if address_part:
                return line.strip()
            # 次の行が住所の場合
            elif i + 1 < len(lines):
                return line.strip() + " " + lines[i + 1].strip()

    # 都道府県名を含む行を探す
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


# -------------------------------------------------------------
# Step 3: 全情報をまとめて辞書として返す
# -------------------------------------------------------------
def parse_meishi(text: str) -> dict:
    """
    OCRテキストから名刺の各項目を抽出し、辞書で返す。
    """
    result = {
        "名前":       extract_name(text),
        "会社名":     extract_company(text),
        "電話番号":   extract_phone(text),
        "メール":     extract_email(text),
        "住所":       extract_address(text),
    }
    return result


# -------------------------------------------------------------
# Step 4: 抽出データをCSVに保存する
# -------------------------------------------------------------
def save_to_csv(data: dict, output_path: str = "meishi_data.csv") -> None:
    """
    抽出データをCSVファイルに保存する。
    既存のCSVがある場合は末尾に追記する。

    Args:
        data: 抽出データの辞書
        output_path: 保存先CSVファイルパス
    """
    df_new = pd.DataFrame([data])

    try:
        # 既存ファイルがあれば読み込んで追記
        df_existing = pd.read_csv(output_path, encoding="utf-8-sig")
        df_combined = pd.concat([df_existing, df_new], ignore_index=True)
    except FileNotFoundError:
        # 新規作成
        df_combined = df_new

    # CSV保存（utf-8-sig はExcelで文字化けしない設定）
    df_combined.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"[完了] CSVに保存しました: {output_path}")


# -------------------------------------------------------------
# メイン処理
# -------------------------------------------------------------
def main():
    # 読み込む画像ファイル名
    image_path = "meishi.jpg"

    print(f"名刺画像を読み込みます: {image_path}\n")

    # OCRでテキスト抽出
    text = extract_text_from_image(image_path)

    # テキストから各情報を解析
    data = parse_meishi(text)

    # 結果表示
    print("=== 抽出結果 ===")
    for key, value in data.items():
        print(f"  {key}: {value if value else '（検出できませんでした）'}")
    print("================\n")

    # CSV保存
    save_to_csv(data, output_path="meishi_data.csv")


if __name__ == "__main__":
    main()
