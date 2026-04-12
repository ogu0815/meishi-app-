FROM python:3.11-slim

WORKDIR /app

# 依存ライブラリをインストール（Tesseract不要）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリのファイルをコピー
COPY . .

# アップロード用フォルダを作成
RUN mkdir -p uploads/saved

# ポート8000を公開
EXPOSE 8000

# gunicorn で本番起動
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--timeout", "120", "app:app"]
