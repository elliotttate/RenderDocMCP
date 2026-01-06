# RenderDoc MCP Server

RenderDoc UI拡張機能として動作するMCPサーバー。AIアシスタントがRenderDocのキャプチャデータにアクセスし、DirectX 11/12のグラフィックスデバッグを支援する。

## アーキテクチャ

**ハイブリッドプロセス分離方式**:

```
Claude/AI Client (stdio)
        │
        ▼
MCP Server Process (標準Python + FastMCP 2.0)
        │ File-based IPC (%TEMP%/renderdoc_mcp/)
        ▼
RenderDoc Process (Extension + File Polling)
```

## プロジェクト構成

```
RenderDocMCP/
├── mcp_server/                        # MCPサーバー
│   ├── server.py                      # FastMCPエントリーポイント
│   ├── config.py                      # 設定
│   └── bridge/
│       └── client.py                  # ファイルベースIPCクライアント
│
├── renderdoc_extension/               # RenderDoc拡張機能
│   ├── __init__.py                    # register()/unregister()
│   ├── extension.json                 # マニフェスト
│   ├── socket_server.py               # ファイルベースIPCサーバー
│   ├── request_handler.py             # リクエスト処理
│   └── renderdoc_facade.py            # RenderDoc APIラッパー
│
└── scripts/
    └── install_extension.py           # 拡張機能インストール
```

## MCPツール

| ツール名 | 説明 |
|---------|------|
| `get_capture_status` | キャプチャ読込状態確認 |
| `get_draw_calls` | ドローコール一覧（階層構造） |
| `get_draw_call_details` | 特定ドローコールの詳細 |
| `get_shader_info` | シェーダーソース/定数バッファ |
| `get_buffer_contents` | バッファデータ取得 |
| `get_texture_info` | テクスチャメタデータ |
| `get_pipeline_state` | パイプライン状態全体 |

## 通信プロトコル

ファイルベースIPC:
- IPCディレクトリ: `%TEMP%/renderdoc_mcp/`
- `request.json`: リクエスト（MCPサーバー → RenderDoc）
- `response.json`: レスポンス（RenderDoc → MCPサーバー）
- `lock`: 書き込み中ロックファイル
- ポーリング間隔: 100ms（RenderDoc側）

## 開発ノート

- RenderDoc内蔵Pythonにはsocket/QtNetworkモジュールがないため、ファイルベースIPCを採用
- RenderDoc拡張機能はPython 3.6標準ライブラリのみ使用
- ReplayControllerへのアクセスは`BlockInvoke`経由で行う

## 参考リンク

- [FastMCP](https://github.com/jlowin/fastmcp)
- [RenderDoc Python API](https://renderdoc.org/docs/python_api/index.html)
- [RenderDoc Extension Registration](https://renderdoc.org/docs/how/how_python_extension.html)
