# Discord Message CLI (`discord-msg`)

権限を持つ Discord チャンネルのメッセージを Discord 公式 HTTP API (v10) で**読み取り専用**で取得し、期間別の集計を行う CLI ツールです。  
人がターミナルで表を確認するほか、AI エージェントやスクリプトが JSON 形式で安全に集計情報だけを取得できるように設計されています。

## 特徴とセキュリティ方針

- **読み取り専用**: `GET /channels/{channel_id}/messages` のみを使用し、投稿・編集・削除・権限変更の機能は一切持ちません。
- **本文・添付の非出力**: 接続確認（`check`）および期間集計（`stats`）の出力（表・JSON ともに）には、メッセージ本文・添付ファイル・トークンを一切含めません。
- **安全なトークン入力**: Bot トークンは環境変数（`DISCORD_BOT_TOKEN`）、対話的な非表示入力（`--prompt-token`）、または標準入力（`--token-stdin`）からのみ受け付けます。コマンドライン引数や設定ファイルには保存・指定できません。
- **標準出力と標準エラーの分離**: 診断メッセージやエラーはすべて標準エラー（`stderr`）に出力され、標準出力（`stdout`）には完全な結果（表または JSON）のみが出力されます。取得途中で失敗した場合は、不完全な集計を標準出力に出さず非ゼロ終了コードで終了します。

## 導入方法

Python 3.10 以上が必要です。`pipx` または `pip` でワンコマンド導入できます。

```powershell
# GitHub リポジトリから直接導入する場合
pipx install git+https://github.com/IT-Engineer-K/discord-message-cli.git

# ローカルのクローンから導入する場合
pipx install .
# または開発モード
pip install -e .
```

## 認証設定

実行前に環境変数 `DISCORD_BOT_TOKEN` を設定するか、実行時に `--prompt-token` を指定してください。

```powershell
# PowerShell で一時的に環境変数へ設定する例
$env:DISCORD_BOT_TOKEN = (Read-Host -AsSecureString "Bot Token" | ConvertFrom-SecureString -AsPlainText)
```

> **必要な Discord Bot 権限**:
> 対象チャンネルに対する `View Channel`（チャンネルを見る）および `Read Message History`（メッセージ履歴を読む）。  
> ※ 接頭辞（`--prefix`）やキーワード（`--keyword`）による本文絞り込みを行う場合は、Discord Developer Portal で Bot の **Message Content Intent** を有効にしてください。

## 使い方

### 1. 接続確認 (`check`)

対象チャンネルの履歴を読み取れるかを確認し、本文を表示せずに結果を返します。

```powershell
# 表形式（既定）
discord-msg check --channel 123456789012345678

# AI / スクリプト向け JSON 形式
discord-msg check --channel 123456789012345678 --format json

# トークンを対話的非表示入力で渡す場合
discord-msg check --channel 123456789012345678 --prompt-token
```

JSON 出力例:

```json
{
  "ok": true,
  "channel_id": "123456789012345678",
  "reachable": true,
  "history_readable": true,
  "has_messages": true
}
```

### 2. 期間別集計 (`stats`)

指定期間のメッセージ件数を集計します。

- **既定期間**: 直近 24 時間（`--since` / `--until` / `--hours` で変更可能。**開始時刻を含み、終了時刻は含まない** `[start, end)`）
- **既定タイムゾーン**: 日本時間 `Asia/Tokyo`（`--tz` で変更可能）
- **集計単位 (`--by`)**: `hour`（時間別・既定）、`day`（日別）、`total`（合計のみ）
- **絞り込み**: `--prefix`（本文の接頭辞）、`--keyword`（本文に含まれるキーワード）、`--ignore-case`（大文字・小文字を区別しない）

```powershell
# 直近24時間の時間別集計（日本時間・表形式）
discord-msg stats --channel 123456789012345678

# 期間と接頭辞・キーワードを指定して AI 向け JSON で取得
discord-msg stats `
  --channel 123456789012345678 `
  --since 2026-11-18T00:00:00+09:00 `
  --until 2026-11-19T00:00:00+09:00 `
  --by hour `
  --prefix "[ALERT]" `
  --keyword "DiskHigh" `
  --format json
```

JSON 出力例:

```json
{
  "channel_id": "123456789012345678",
  "period": {
    "start": "2026-11-18T09:00:00+09:00",
    "end": "2026-11-18T12:00:00+09:00"
  },
  "timezone": "Asia/Tokyo",
  "granularity": "hour",
  "filter": {
    "prefix": "[ALERT]",
    "keyword": "DiskHigh",
    "ignore_case": false
  },
  "buckets": [
    {
      "label": "2026-11-18 09:00",
      "start": "2026-11-18T09:00:00+09:00",
      "end": "2026-11-18T10:00:00+09:00",
      "count": 2
    },
    {
      "label": "2026-11-18 10:00",
      "start": "2026-11-18T10:00:00+09:00",
      "end": "2026-11-18T11:00:00+09:00",
      "count": 0
    },
    {
      "label": "2026-11-18 11:00",
      "start": "2026-11-18T11:00:00+09:00",
      "end": "2026-11-18T12:00:00+09:00",
      "count": 3
    }
  ],
  "total": 5,
  "scanned_in_period": 8
}
```

## ローカル設定ファイル（任意・非機密値のみ）

毎回 `--channel` を指定する代わりに、環境変数 `DISCORD_CHANNEL_ID` またはローカル設定ファイル（Windows の既定パス: `%APPDATA%\discord-msg\config.json`）に既定のチャンネル ID とタイムゾーンを記述できます。  
※ セキュリティ保護のため、設定ファイルに `token` / `bot_token` キーが含まれている場合はエラーとして拒否します。

```json
{
  "channel": "123456789012345678",
  "tz": "Asia/Tokyo"
}
```

## 終了コード一覧

| 終了コード | エラーコード | 意味 |
| :--- | :--- | :--- |
| `0` | - | 正常終了（履歴が空の場合も含む） |
| `2` | `INVALID_ARGS` | 引数・期間指定・設定ファイルの形式エラー |
| `3` | `AUTH_FAILED` | トークン未指定または認証失敗 (`401 Unauthorized`) |
| `4` | `PERMISSION_DENIED` | チャンネル閲覧・履歴読み取り権限不足 (`403 Forbidden`) |
| `5` | `NOT_FOUND` | チャンネルが存在しないかアクセス不可 (`404 Not Found`) |
| `6` | `RATE_LIMITED` | レート制限の再試行上限超過 (`429 Too Many Requests`) |
| `7` | `FETCH_FAILED` | 通信エラーやページ送り途中の API エラー（不完全な集計は返さない） |

## テストの実行

テストにはすべて架空のチャンネル ID・架空の通知データを使用しており、外部通信を行わずに実行できます。

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```
