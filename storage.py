# =============================================================
# storage.py  保存先の切り替えモジュール
#
# ★ 保存先を変えるには STORAGE_MODE だけ書き換えるだけ ★
#   "google"  → Google スプレッドシート
#   "excel"   → Excel ファイル（meishi_data.xlsx）
#   "csv"     → CSV ファイル（meishi_data.csv）← 従来の動作
# =============================================================

import os
import re
import pandas as pd

# -------------------------------------------------------
# ★ ここを変えるだけで保存先が切り替わる ★
STORAGE_MODE = "google"   # "google" / "excel" / "csv"
# -------------------------------------------------------

# --- Google スプレッドシート設定 ---
GOOGLE_CREDENTIALS_FILE = "credentials.json"   # サービスアカウントのJSONファイル
GOOGLE_SPREADSHEET_NAME = "名刺管理データ"      # スプレッドシートのタイトル
GOOGLE_SHEET_NAME       = "名刺一覧"            # シート名

# --- Excel / CSV 設定 ---
EXCEL_FILE = "meishi_data.xlsx"
CSV_FILE   = "meishi_data.csv"

# ヘッダー列（全モード共通）
COLUMNS = ["ファイル名", "画像", "名前", "会社名", "部署", "役職", "電話番号1", "電話番号2", "メール", "住所"]

# アプリのベースURL（スプレッドシートの画像リンク生成に使用）
# Render.com デプロイ後は環境変数 APP_BASE_URL に本番URLを設定する
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")


# =============================================================
# Google スプレッドシート操作
# =============================================================

def _get_gspread_sheet():
    """認証してワークシートを返す"""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=scopes)
    client = gspread.authorize(creds)

    try:
        spreadsheet = client.open(GOOGLE_SPREADSHEET_NAME)
    except gspread.SpreadsheetNotFound:
        # スプレッドシートが存在しない場合は新規作成
        spreadsheet = client.create(GOOGLE_SPREADSHEET_NAME)
        print(f"[Google] スプレッドシートを新規作成しました: {GOOGLE_SPREADSHEET_NAME}")

    try:
        sheet = spreadsheet.worksheet(GOOGLE_SHEET_NAME)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=GOOGLE_SHEET_NAME, rows=1000, cols=10)
        _setup_sheet_header(sheet, spreadsheet.id)
        print(f"[Google] シートを新規作成しました: {GOOGLE_SHEET_NAME}")

    return sheet


def _setup_sheet_header(sheet, spreadsheet_id: str):
    """
    スプレッドシートの1行目にアプリURLリンクを埋め込み、
    2行目にカラムヘッダーを設定する。
    """
    import socket
    # MacのIPアドレスを自動取得
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip.startswith("127."):
            ip = "localhost"
    except Exception:
        ip = "localhost"

    app_url = f"http://{ip}:8000"

    # 1行目：アプリURLをHYPERLINKで埋め込む（USER_ENTEREDで数式として評価）
    sheet.update(
        values=[[f'=HYPERLINK("{app_url}","▶ 名刺管理アプリを開く → {app_url}")', "", "", "", "", "", "", "", ""]],
        range_name="A1",
        value_input_option="USER_ENTERED"
    )

    # 書式：リンク行を目立たせる（背景色・太字）
    spreadsheet = sheet.spreadsheet
    spreadsheet.batch_update({
        "requests": [
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet.id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(COLUMNS),
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.149, "green": 0.376, "blue": 0.871},
                            "textFormat": {
                                "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                                "bold": True,
                                "fontSize": 11,
                            },
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            }
        ]
    })

    # 2行目：カラムヘッダー
    sheet.append_row(COLUMNS)


def _google_append(data: dict):
    """Google スプレッドシートに1行追記する"""
    sheet = _get_gspread_sheet()

    # 2行目のヘッダーが最新のCOLUMNSと一致していなければ更新
    all_values = sheet.get_all_values()
    if len(all_values) < 2:
        sheet.append_row(COLUMNS)
    elif all_values[1] != COLUMNS:
        sheet.update(values=[COLUMNS], range_name="A2")

    row = [data.get(col, "") for col in COLUMNS]

    # 画像列（index=1）をHYPERLINKに変換（スプレッドシートからクリックで画像を開ける）
    img_filename = data.get("画像", "")
    if img_filename:
        img_url = f"{APP_BASE_URL}/image/{img_filename}"
        row[1] = f'=HYPERLINK("{img_url}","🖼 画像を見る")'

    sheet.append_row(row, value_input_option="USER_ENTERED")


def _google_read_all() -> list[dict]:
    """Google スプレッドシートの全データを読み込む。
    1行目はURLリンク行、2行目はヘッダーのため、3行目以降をデータとして読む。
    ヘッダーが古い場合は自動で更新する。
    """
    sheet = _get_gspread_sheet()

    # 2行目ヘッダーが最新のCOLUMNSと一致していなければ更新
    all_values = sheet.get_all_values()
    if len(all_values) >= 2 and all_values[1] != COLUMNS:
        sheet.update(values=[COLUMNS], range_name="A2")

    records = sheet.get_all_records(head=2)

    # 画像列のHYPERLINKからファイル名を復元する（1回のAPI呼び出しで取得）
    img_col_idx = COLUMNS.index("画像")
    num_data = len(records)
    if num_data > 0:
        col_letter = chr(ord("A") + img_col_idx)
        range_name = f"{col_letter}3:{col_letter}{num_data + 2}"
        formulas = sheet.get(range_name, value_render_option="FORMULA")
        for i, formula_row in enumerate(formulas or []):
            if i < len(records) and formula_row:
                formula = str(formula_row[0])
                m = re.search(r'/image/([^"]+)"', formula)
                if m:
                    records[i]["画像"] = m.group(1)

    return records


def _google_delete(index: int):
    """Google スプレッドシートの指定行を削除する（ヘッダー行を除いた0始まりのindex）"""
    sheet = _get_gspread_sheet()
    # 1行目=URLリンク、2行目=ヘッダー、3行目以降=データ
    sheet.delete_rows(index + 3)


def _google_update(index: int, data: dict):
    """Google スプレッドシートの指定行を更新する"""
    sheet = _get_gspread_sheet()
    row_num = index + 3  # 1行目=URL、2行目=ヘッダー、3行目以降=データ
    row = [data.get(col, "") for col in COLUMNS]
    sheet.update(values=[row], range_name=f"A{row_num}")


# =============================================================
# Excel 操作
# =============================================================

def _excel_append(data: dict):
    """Excel ファイルに1行追記する"""
    df_new = pd.DataFrame([data])
    if os.path.exists(EXCEL_FILE):
        df_existing = pd.read_excel(EXCEL_FILE)
        df = pd.concat([df_existing, df_new], ignore_index=True)
    else:
        df = df_new
    df.to_excel(EXCEL_FILE, index=False)


def _excel_read_all() -> list[dict]:
    """Excel ファイルの全データを読み込む"""
    if not os.path.exists(EXCEL_FILE):
        return []
    df = pd.read_excel(EXCEL_FILE).fillna("")
    return df.to_dict(orient="records")


def _excel_delete(index: int):
    """Excel ファイルの指定行を削除する"""
    df = pd.read_excel(EXCEL_FILE)
    df = df.drop(index=index).reset_index(drop=True)
    df.to_excel(EXCEL_FILE, index=False)


def _excel_update(index: int, data: dict):
    """Excel ファイルの指定行を更新する"""
    df = pd.read_excel(EXCEL_FILE)
    for col in COLUMNS:
        if col in df.columns:
            df.at[index, col] = data.get(col, "")
    df.to_excel(EXCEL_FILE, index=False)


# =============================================================
# CSV 操作
# =============================================================

def _csv_append(data: dict):
    """CSV ファイルに1行追記する"""
    df_new = pd.DataFrame([data])
    if os.path.exists(CSV_FILE):
        df_existing = pd.read_csv(CSV_FILE, encoding="utf-8-sig")
        df = pd.concat([df_existing, df_new], ignore_index=True)
    else:
        df = df_new
    df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")


def _csv_read_all() -> list[dict]:
    """CSV ファイルの全データを読み込む"""
    if not os.path.exists(CSV_FILE):
        return []
    return pd.read_csv(CSV_FILE, encoding="utf-8-sig").fillna("").to_dict(orient="records")


def _csv_delete(index: int):
    """CSV ファイルの指定行を削除する"""
    df = pd.read_csv(CSV_FILE, encoding="utf-8-sig")
    df = df.drop(index=index).reset_index(drop=True)
    df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")


def _csv_update(index: int, data: dict):
    """CSV ファイルの指定行を更新する"""
    df = pd.read_csv(CSV_FILE, encoding="utf-8-sig")
    for col in COLUMNS:
        if col in df.columns:
            df.at[index, col] = data.get(col, "")
    df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")


# =============================================================
# オフライン判定
# =============================================================

def _is_online() -> bool:
    """インターネット接続があるか確認する"""
    import socket
    try:
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except Exception:
        return False


# =============================================================
# 公開インターフェース（app.py はこれだけ呼べばよい）
# =============================================================

def append_record(data: dict):
    """名刺データを1件保存する。
    Google モードでオフラインの場合は自動的にCSVにフォールバック。
    オンライン復帰時に自動でスプレッドシートに同期する。
    """
    if STORAGE_MODE == "google":
        if _is_online():
            _google_append(data)
            # オフライン中に溜まったCSVをスプレッドシートに同期
            _sync_offline_queue()
        else:
            print("[オフライン] インターネット未接続のためCSVに一時保存します")
            _csv_append(data)
    elif STORAGE_MODE == "excel":
        _excel_append(data)
    else:
        _csv_append(data)


def read_all_records() -> list[dict]:
    """全件取得する。
    Google モードでオフラインの場合はCSVから読み込む。
    """
    if STORAGE_MODE == "google":
        if _is_online():
            _sync_offline_queue()
            return _google_read_all()
        else:
            print("[オフライン] CSVからデータを読み込みます")
            return _csv_read_all()
    elif STORAGE_MODE == "excel":
        return _excel_read_all()
    else:
        return _csv_read_all()


def delete_record(index: int):
    """指定インデックスのレコードを削除する"""
    if STORAGE_MODE == "google":
        if _is_online():
            _google_delete(index)
        else:
            _csv_delete(index)
    elif STORAGE_MODE == "excel":
        _excel_delete(index)
    else:
        _csv_delete(index)


def update_record(index: int, data: dict):
    """指定インデックスのレコードを更新する"""
    if STORAGE_MODE == "google":
        if _is_online():
            _google_update(index, data)
        else:
            _csv_update(index, data)
    elif STORAGE_MODE == "excel":
        _excel_update(index, data)
    else:
        _csv_update(index, data)


# =============================================================
# オフラインキューの同期
# =============================================================

def _sync_offline_queue():
    """オフライン中にCSVに溜まったデータをスプレッドシートに同期する"""
    if not os.path.exists(CSV_FILE):
        return

    try:
        df = pd.read_csv(CSV_FILE, encoding="utf-8-sig").fillna("")
        if df.empty:
            return

        sheet = _get_gspread_sheet()
        online_records = sheet.get_all_records()

        # スプレッドシートにないデータだけ追記する（ファイル名で重複チェック）
        online_filenames = {r.get("ファイル名", "") for r in online_records}
        new_rows = df[~df["ファイル名"].isin(online_filenames)]

        if new_rows.empty:
            return

        for _, row in new_rows.iterrows():
            sheet.append_row([row.get(col, "") for col in COLUMNS])

        # 同期完了したらCSVを削除
        os.remove(CSV_FILE)
        print(f"[同期完了] {len(new_rows)} 件をスプレッドシートに同期しました")

    except Exception as e:
        print(f"[同期エラー] {e}")
