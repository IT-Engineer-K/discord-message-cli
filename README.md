# Discord Message CLI (`discord-msg`)

権限を持つ Discord チャンネル／スレッドのメッセージを Discord 公式 HTTP API (v10) で取得・期間別集計・投稿・スレッド作成・返信待機する汎用 CLI ツール兼 Python ライブラリです。  
人がターミナルで表を確認するほか、AI エージェントや外部スクリプト／Python アプリケーションが JSON 形式または Python API で安全に利用できるように設計されています。

## 特徴とセキュリティ方針

- **最小権限の API 操作**: メッセージ取得（`GET /channels/{id}/messages`）、チャンネル一覧取得（`GET /users/@me/guilds`, `GET /guilds/{id}/channels`）、メッセージ投稿（`POST /channels/{id}/messages`）、既存メッセージからのスレッド作成（`POST /channels/{id}/messages/{msg_id}/threads`）のみを行い、削除・編集・権限変更の機能は持ちません。また HTTP リダイレクトの自動追従を無効化し、トークンの外部漏えいを防ぎます。
- **メンションは既定で無効**: `send` コマンドおよび `send_message()` による投稿時は、デフォルトで `allowed_mentions: {"parse": []}` を送信し、本文中に `@everyone`・`@here`・ユーザー/ロールメンションが含まれていても通知が飛ばない安全設計です（明示的に `--allow-mentions` / `allow_mentions=True` を指定した場合のみ有効化）。
- **集計・接続確認・投稿時の本文非出力**: 接続確認（`check`）・期間集計（`stats`）・メッセージ投稿（`send`）の出力（表・JSON ともに）には、メッセージ本文・添付ファイル・トークンを一切含めません。返信取得系（`messages` / `wait`）は指定範囲（`--after` 以降または上限件数以内）のメッセージのみを取得し、指定範囲外の一括収集は行いません。
- **安全なトークン入力**: Bot トークンは環境変数（`DISCORD_BOT_TOKEN`）、対話的な非表示入力（`--prompt-token`）、または標準入力（`--token-stdin`）からのみ受け付けます。コマンドライン引数や設定ファイルには保存・指定できません。
- **標準出力と標準エラーの分離**: 診断メッセージやエラーはすべて標準エラー（`stderr`）に出力され、標準出力（`stdout`）には完全な結果（表または JSON）のみが出力されます。取得途中で失敗した場合は、不完全な集計を標準出力に出さず非ゼロ終了コードで終了します。

## 導入方法

Python 3.10 以上が必要です。`pipx` または `pip` でワンコマンド導入できます。

```powershell
# PyPI から導入する場合（推奨）
pipx install discord-message-cli
# または
pip install discord-message-cli

# GitHub リポジトリから直接導入する場合
pipx install git+https://github.com/IT-Engineer-K/discord-message-cli.git

# ローカルのクローンから導入する場合
pipx install .
```

## 認証設定

実行前に環境変数 `DISCORD_BOT_TOKEN` を設定するか、実行時に `--prompt-token` を指定してください。

Windows で次回以降も使えるようにユーザー環境変数へ保存したい場合は、`setup` サブコマンドを使用できます（非表示入力で受け取り、新しいターミナルから有効になります）：

```powershell
# 対話入力で Windows のユーザー環境変数 DISCORD_BOT_TOKEN に保存
discord-msg setup

# 保存した環境変数を削除する場合
discord-msg setup --clear

# PowerShell で現在のセッションのみ一時的に設定する例
$env:DISCORD_BOT_TOKEN = (Read-Host -AsSecureString "Bot Token" | ConvertFrom-SecureString -AsPlainText)
```

> **必要な Discord Bot 権限・前提条件**:
> - **権限**: 対象チャンネルに対して `View Channel`（チャンネルを見る）、`Read Message History`（メッセージ履歴を読む・`check`/`stats`/`messages`/`wait` 用）、`Send Messages`（チャンネルへの投稿用）、`Create Public Threads`（`thread create` 用）、`Send Messages in Threads`（スレッドへの投稿用）が必要です。
> - **Message Content Intent**: 他ユーザーのメッセージ本文を取得・判定する場合（`stats --prefix/--keyword` および `messages` / `wait` で本文を読み取る場合）は、Discord Developer Portal の Bot 設定で **Message Content Intent** を有効にしてください（未有効の場合、他ユーザーの `content` が空文字になることがあります）。
> - **レート制限とポーリング間隔**: `wait` は REST API ポーリング方式（Gateway 常時接続ではない）で動作します。HTTP 429 受信時は `retry_after` に従って自動リトライしますが、過度な API 呼び出しを避けるため `--poll-interval` は既定の `2` 秒以上を推奨します。

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

### 3. メッセージ投稿 (`send`)

指定チャンネルまたはスレッドにメッセージを投稿します。**デフォルトではすべてのメンション（`@everyone` / `@here` / ユーザー / ロール）が無効化**されます。

```powershell
# 位置引数または --message (-m) で投稿（メンションはデフォルト禁止）
discord-msg send --channel 123456789012345678 "バッチ処理が完了しました"
discord-msg send --channel 123456789012345678 -m "バッチ処理が完了しました" --format json

# スレッドIDを指定して投稿（--channel とは同時指定不可）
discord-msg send --thread 1309500000000000001 "確認事項があります" --format json

# 標準入力から本文を渡して投稿
"複数行のメッセージ" | discord-msg send --channel 123456789012345678 --stdin

# メンションを明示的に許可する場合のみ --allow-mentions を指定
discord-msg send --channel 123456789012345678 "@here 緊急アラート" --allow-mentions
```

JSON 出力例:

```json
{
  "ok": true,
  "channel_id": "123456789012345678",
  "message_id": "1308123456789012345",
  "timestamp": "2026-11-19T04:55:00.000000+00:00",
  "mentions_allowed": false
}
```

### 4. スレッド作成 (`thread create`)

既存のメッセージを起点にスレッドを作成します。

```powershell
discord-msg thread create `
  --channel 123456789012345678 `
  --message-id 1308123456789012345 `
  --name "確認スレッド" `
  --auto-archive-duration 1440 `
  --format json
```

JSON 出力例:

```json
{
  "ok": true,
  "thread_id": "1309500000000000001",
  "parent_channel_id": "123456789012345678",
  "source_message_id": "1308123456789012345",
  "name": "確認スレッド",
  "type": 11,
  "auto_archive_duration": 1440,
  "archived": false
}
```

### 5. メッセージ取得 (`messages`)

指定チャンネルまたはスレッドのメッセージを `--after`（指定した `message_id` より後）に限定して取得します。`--author-id` / `--exclude-author-id` / `--exclude-bots` で絞り込みができ、次回再開用の `cursor`（最後に確認した `message_id`）を返します。

```powershell
discord-msg messages `
  --thread 1309500000000000001 `
  --after 1309000000000000100 `
  --author-id 222333444555666777 `
  --exclude-bots `
  --format json
```

JSON 出力例:

```json
{
  "ok": true,
  "channel_id": "1309500000000000001",
  "after": "1309000000000000100",
  "cursor": "1309000000000000205",
  "count": 1,
  "messages": [
    {
      "message_id": "1309000000000000205",
      "channel_id": "1309500000000000001",
      "author_id": "222333444555666777",
      "author_username": "alice",
      "author_display_name": "Alice",
      "author_bot": false,
      "timestamp": "2026-11-25T00:02:00.000000+00:00",
      "content": "はい、進めてください。",
      "referenced_message_id": "1309000000000000100"
    }
  ]
}
```

### 6. 返信待機 (`wait`)

指定チャンネルまたはスレッドに条件を満たす新しいメッセージが届くか、`--timeout` 秒が経過するまでポーリング待機します。タイムアウト時も終了コード `0` で `timed_out: true` と最新の `cursor` を JSON で返すため、そのまま再開できます。

```powershell
discord-msg wait `
  --thread 1309500000000000001 `
  --after 1309000000000000100 `
  --exclude-bots `
  --timeout 60 `
  --poll-interval 2 `
  --format json
```

### 7. Python API としての利用

外部 Python プロジェクトから `discord_msg` を直接 import して利用できます（トークン未指定時は環境変数 `DISCORD_BOT_TOKEN` を参照します）。

```python
from discord_msg import (
    create_thread,
    list_messages,
    send_message,
    wait_for_messages,
)

# 1. 既存メッセージからスレッド作成
thread = create_thread(
    channel_id="123456789012345678",
    message_id="1308123456789012345",
    name="確認スレッド",
)

# 2. スレッドへ質問送信
question = send_message(thread_id=thread.thread_id, content="この方針で進めてよいですか？")

# 3. 返信が届くまで待機（Bot自身を除外）
waited = wait_for_messages(
    thread_id=thread.thread_id,
    after=question.message_id,
    exclude_bots=True,
    timeout=60.0,
    poll_interval=2.0,
)

# 4. 返却された cursor を使って次回取得を再開
next_batch = list_messages(
    thread_id=thread.thread_id,
    after=waited.cursor,
    exclude_bots=True,
)
```

`DiscordClient(token)` を直接生成してクライアントインスタンスを使い回すことも可能です。

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
