# auto-zenn-pipeline
自己進化型・技術ブログ自動生成システム(Auto-Zenn-Pipeline)

## 確認済みの外部仕様

| 項目 | 結果 | 出典 | 確認日 | 反映先 |
|---|---|---|---|---|
| モデル名の種別(claude-sonnet-5-5 / claude-opus-5-5 が固定スナップショットか別名か、スナップショット ID) | 公式の一覧では「Claude モデルの ID はすべて固定(pinned)のスナップショットで、4.6 世代以降の日付なしの ID も含む」とある。claude-sonnet-5-5 と claude-opus-5-5 は日付なしの ID そのものが固定スナップショットで、別の日付付きスナップショット ID は無い(alias の行も同じ ID を繰り返している) | https://platform.claude.com/docs/en/about-claude/models/overview | 2026-10-15 | `pipeline.toml` |
| モデルのコンテキスト長・出力上限・料金 | 両モデルとも コンテキスト 1M トークン、出力上限(同期 Messages API)128K トークン。料金は claude-sonnet-5-5 が入力 $2 / 出力 $10、claude-opus-5-5 が入力 $4 / 出力 $20(100 万トークンあたり、基本料金)。長いコンテキストやキャッシュの料金は未確認 | https://platform.claude.com/docs/en/about-claude/models/overview | 2026-10-15 | `pipeline.toml の limits` |
| tool_choice による tool 強制の書き方と、SDK の非ストリーミングでの max_tokens の上限 | 書き方は tool_choice={"type": "tool", "name": "<tool 名>"}。ただし公式は claude-opus-5-5 と claude-sonnet-5-5 では type が any と tool の強制が 400 エラーになると書いており、代わりに auto と strict tool use か structured outputs を使うとある。SDK は非ストリーミングの要求が約 10 分より長くかかると見込まれると ValueError を出す(stream=True か timeout の指定で回避できる)。max_tokens の具体的な上限の数値は公式の README とドキュメントに見つからず未確認 | https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python | 2026-10-15 | `llm.py` |
| anthropic Python SDK の現在の版 | PyPI の最新は 1.14.0(2026-10-14 公開) | https://pypi.org/project/anthropic/ | 2026-10-15 | `uv.lock` |
| Zenn の slug・title の長さ・topics・emoji の制約 | slug は a-z0-9 とハイフンとアンダースコアの 12〜50 字。emoji は 1 文字だけ。title の長さと topics の件数・文字種の制限は、読んだページには書かれておらず未確認 | https://zenn.dev/zenn/articles/zenn-cli-guide https://zenn.dev/zenn/articles/what-is-slug | 2026-10-15 | `article.py` |
| Zenn の GitHub 連携(private リポジトリ、デプロイするブランチ) | リポジトリの公開設定は Public でも Private でもよい。デプロイするブランチは Zenn のダッシュボードのリポジトリ設定で確認・変更し、登録したブランチが更新されるとデプロイされる。既定のブランチ名は書かれておらず未確認 | https://zenn.dev/zenn/articles/connect-to-github | 2026-10-15 | `README` |
| GITHUB_TOKEN(contents: write)でのタグの push | GITHUB_TOKEN が起こしたイベントは新しいワークフロー実行を作らない(例外は workflow_dispatch と repository_dispatch)。contents: write でタグを push できるかは、読んだページに明記がなく未確認 | https://docs.github.com/en/actions/concepts/security/github_token | 2026-10-15 | `01_generate_draft.yml` |
| 使う Action の現在の版(actions/checkout、astral-sh/setup-uv、rhysd/actionlint) | 各リポジトリの最新リリース: actions/checkout は v7.0.1、astral-sh/setup-uv は v10.3.0、rhysd/actionlint は v1.7.12(リリースのページには年が表示されていない) | https://github.com/actions/checkout/releases/latest https://github.com/astral-sh/setup-uv/releases/latest https://github.com/rhysd/actionlint/releases/latest | 2026-10-15 | `ci.yml / 01_generate_draft.yml` |
