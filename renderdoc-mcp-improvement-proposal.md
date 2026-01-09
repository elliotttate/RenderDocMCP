# RenderDoc MCP 改善提案

## 背景

UnityプロジェクトでRenderDocキャプチャを分析する際、以下の課題がある：

1. **UIノイズ問題**: Unity Editorからキャプチャすると、`GUI.Repaint`や`UIR.DrawChain`などのEditor UI描画が大量に含まれ、実際のゲーム描画（`Camera.Render`配下）を探すのが困難
2. **レスポンスサイズ問題**: `get_draw_calls(include_children=true)`の結果が70KB超になり、LLMのコンテキストを圧迫
3. **探索の非効率性**: 特定のシェーダーやテクスチャを使用しているドローコールを見つけるのに、全ドローコールを1つずつ確認する必要がある

## 改善提案

### 1. マーカーフィルタリング（優先度: 高）

特定のマーカー配下のみ、または特定のマーカーを除外して取得する機能。

```python
get_draw_calls(
    include_children=True,
    marker_filter="Camera.Render",  # このマーカー配下のみ取得
    exclude_markers=["GUI.Repaint", "UIR.DrawChain", "UGUI.Rendering"]
)
```

**ユースケース**:
- Unity Editorキャプチャからゲーム描画のみを抽出
- 特定のレンダリングパス（Shadows, PostProcess等）のみを調査

**期待される効果**:
- レスポンスサイズを10-20%に削減
- LLMが直接解析可能なサイズに収まる

---

### 2. event_id範囲指定（優先度: 高）

特定のevent_id範囲のみを取得する機能。

```python
get_draw_calls(
    event_id_min=7372,
    event_id_max=7600,
    include_children=True
)
```

**ユースケース**:
- `Camera.Render`のevent_idが判明している場合、その周辺のみを取得
- 問題のあるドローコール周辺を詳細に調査

**期待される効果**:
- 必要な部分だけを高速に取得
- 段階的な探索が可能に

---

### 3. シェーダー/テクスチャ/リソースによる逆引き検索（優先度: 中）

特定のリソースを使用しているドローコールを検索する機能。

```python
# シェーダー名で検索（部分一致）
find_draws_by_shader(shader_name="Toon")

# テクスチャ名で検索（部分一致）
find_draws_by_texture(texture_name="CharacterSkin")

# リソースIDで検索（完全一致）
find_draws_by_resource(resource_id="ResourceId::12345")
```

**返り値例**:
```json
{
  "matches": [
    {"event_id": 7538, "name": "DrawIndexed", "match_reason": "pixel_shader contains 'Toon'"},
    {"event_id": 7620, "name": "DrawIndexed", "match_reason": "pixel_shader contains 'Toon'"}
  ],
  "total_matches": 2
}
```

**ユースケース**:
- 「このシェーダーを使っているドローはどれ？」という最も一般的な質問に直接回答
- 特定のテクスチャがどこで使われているか追跡
- シェーダーバグの影響範囲を特定

---

### 4. フレームサマリー取得（優先度: 中）

フレーム全体の概要を取得する機能。

```python
get_frame_summary()
```

**返り値例**:
```json
{
  "api": "D3D11",
  "total_events": 7763,
  "statistics": {
    "draw_calls": 64,
    "dispatches": 193,
    "clears": 5,
    "copies": 8
  },
  "top_level_markers": [
    {"name": "WaitForRenderJobs", "event_id": 118},
    {"name": "CustomRenderTextures.Update", "event_id": 6451},
    {"name": "Camera.Render", "event_id": 7372},
    {"name": "UIR.DrawChain", "event_id": 6484}
  ],
  "render_targets": [
    {"resource_id": "ResourceId::22573", "name": "MainRT", "resolution": "1920x1080"},
    {"resource_id": "ResourceId::22585", "name": "ShadowMap", "resolution": "2048x2048"}
  ],
  "unique_shaders": {
    "vertex": 12,
    "pixel": 15,
    "compute": 8
  }
}
```

**ユースケース**:
- 探索の起点として全体像を把握
- どのマーカー配下を詳しく見るか判断
- パフォーマンス概要の把握

---

### 5. ドローコールのみ取得モード（優先度: 中）

マーカー（PushMarker/PopMarker）を除外し、実際の描画コールのみを取得する機能。

```python
get_draw_calls(
    only_actions=True,  # マーカーを除外
    flags_filter=["Drawcall", "Dispatch"]  # 特定のフラグを持つもののみ
)
```

**ユースケース**:
- ドローコールの総数と一覧だけ欲しい場合
- Compute Shader（Dispatch）のみを調査したい場合

---

### 6. バッチパイプラインステート取得（優先度: 低）

複数のevent_idのパイプラインステートを一度に取得する機能。

```python
get_multiple_pipeline_states(event_ids=[7538, 7558, 7450, 7458])
```

**返り値例**:
```json
{
  "states": {
    "7538": { /* pipeline state */ },
    "7558": { /* pipeline state */ },
    "7450": { /* pipeline state */ },
    "7458": { /* pipeline state */ }
  }
}
```

**ユースケース**:
- 複数のドローコールを比較分析
- 差分調査（正常なドローと異常なドローの比較）

---

## 優先度まとめ

| 優先度 | 機能 | 実装難易度 | 効果 |
|--------|------|-----------|------|
| **高** | マーカーフィルタリング | 中 | UIノイズ除去で劇的に改善 |
| **高** | event_id範囲指定 | 低 | 部分取得で高速化 |
| **中** | シェーダー/テクスチャ逆引き | 高 | 最も多いユースケースを直接サポート |
| **中** | フレームサマリー | 中 | 探索の起点として有用 |
| **中** | ドローコールのみ取得 | 低 | シンプルなフィルタリング |
| **低** | バッチ取得 | 低 | 効率化だが必須ではない |

## Unity固有のフィルタリングプリセット（オプション）

Unity専用のプリセットがあると便利：

```python
get_draw_calls(
    preset="unity_game_rendering"
)
```

**プリセット内容**:
- `marker_filter`: "Camera.Render"
- `exclude_markers`: ["GUI.Repaint", "UIR.DrawChain", "GUITexture.Draw", "UGUI.Rendering.RenderOverlays", "PlayerEndOfFrame", "EditorLoop"]

---

## 実装の参考：現在のワークフローの問題点

### 現状のフロー

```
1. get_draw_calls(include_children=true)
   → 76KB のJSONが返る（ファイルに保存される）

2. ファイルを外部ツール（Python等）で解析
   → Camera.Render の event_id を特定（例: 7372）

3. 手動でevent_id範囲を指定して詳細調査
   → get_pipeline_state(7538), get_shader_info(7538, "pixel"), ...
```

### 改善後の理想フロー

```
1. get_frame_summary()
   → Camera.Render が event_id: 7372 にあることが分かる

2. get_draw_calls(marker_filter="Camera.Render", exclude_markers=[...])
   → 必要なドローコールのみ取得（数KB）

3. find_draws_by_shader(shader_name="MyShader")
   → 該当するevent_idが直接返る

4. get_pipeline_state(event_id) で詳細確認
```

---

## 補足：スキップすべきUnityマーカー一覧

Unity Editorからのキャプチャで除外すべきマーカー：

| マーカー名 | 説明 |
|-----------|------|
| `GUI.Repaint` | IMGUI描画 |
| `UIR.DrawChain` | UI Toolkit描画 |
| `GUITexture.Draw` | GUIテクスチャ描画 |
| `UGUI.Rendering.RenderOverlays` | uGUIオーバーレイ |
| `PlayerEndOfFrame` | フレーム終了処理 |
| `EditorLoop` | エディタループ処理 |

逆に、重要なマーカー：

| マーカー名 | 説明 |
|-----------|------|
| `Camera.Render` | メインカメラ描画の起点 |
| `Drawing` | 描画フェーズ |
| `Render.OpaqueGeometry` | 不透明オブジェクト描画 |
| `Render.TransparentGeometry` | 半透明オブジェクト描画 |
| `RenderForward.RenderLoopJob` | フォワードレンダリングのドローコール群 |
| `Camera.RenderSkybox` | スカイボックス描画 |
| `Camera.ImageEffects` | ポストプロセス |
| `Shadows.RenderShadowMap` | シャドウマップ生成 |
