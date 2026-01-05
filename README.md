# RenderDoc MCP Server

RenderDoc UI拡張機能として動作するMCPサーバー。AIアシスタントがRenderDocのキャプチャデータにアクセスし、DirectX 11/12のグラフィックスデバッグを支援する。

## アーキテクチャ

```
Claude/AI Client (stdio)
        │
        ▼
MCP Server Process (Python + FastMCP 2.0)
        │ TCP Socket (127.0.0.1:19876)
        ▼
RenderDoc Process (Extension)
```

## セットアップ

### 1. RenderDoc拡張機能のインストール

```bash
python scripts/install_extension.py
```

拡張機能は `%APPDATA%\qrenderdoc\extensions\renderdoc_mcp_bridge` にインストールされる。

### 2. RenderDocで拡張機能を有効化

1. RenderDocを起動
2. Tools > Manage Extensions
3. "RenderDoc MCP Bridge" を有効化
4. RenderDocを再起動

### 3. MCPサーバーのインストール

```bash
uv tool install --editable .
uv tool update-shell  # PATHに追加
```

シェルを再起動すると `renderdoc-mcp` コマンドが使えるようになる。

> **Note**: `--editable` を付けると、ソースコードの変更が即座に反映される（開発時に便利）。
> 安定版としてインストールする場合は `uv tool install .` を使用。

### 4. MCPクライアントの設定

#### Claude Desktop

`claude_desktop_config.json` に追加:

```json
{
  "mcpServers": {
    "renderdoc": {
      "command": "renderdoc-mcp"
    }
  }
}
```

#### Claude Code

`.mcp.json` に追加:

```json
{
  "mcpServers": {
    "renderdoc": {
      "command": "renderdoc-mcp"
    }
  }
}
```

## 使い方

1. RenderDocを起動し、キャプチャファイル (.rdc) を開く
2. MCPクライアント (Claude等) から RenderDoc のデータにアクセス

## MCPツール一覧

| ツール | 説明 |
|--------|------|
| `get_capture_status` | キャプチャの読み込み状態を確認 |
| `get_draw_calls` | ドローコール一覧を階層構造で取得 |
| `get_draw_call_details` | 特定のドローコールの詳細情報を取得 |
| `get_shader_info` | シェーダーのソースコード・定数バッファの値を取得 |
| `get_buffer_contents` | バッファの内容を取得 (Base64) |
| `get_texture_info` | テクスチャのメタデータを取得 |
| `get_pipeline_state` | パイプライン状態を取得 |

## 使用例

### ドローコール一覧の取得

```
get_draw_calls(include_children=true)
```

### シェーダー情報の取得

```
get_shader_info(event_id=123, stage="pixel")
```

### パイプライン状態の取得

```
get_pipeline_state(event_id=123)
```

## 環境変数

| 変数名 | デフォルト値 | 説明 |
|--------|-------------|------|
| `RENDERDOC_MCP_HOST` | `127.0.0.1` | RenderDoc拡張機能のホスト |
| `RENDERDOC_MCP_PORT` | `19876` | RenderDoc拡張機能のポート |

## 要件

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- RenderDoc 1.20+
- Windows (DirectX 11/12対象)

## ライセンス

MIT
