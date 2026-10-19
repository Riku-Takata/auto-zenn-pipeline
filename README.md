# auto-zenn-pipeline
自己進化型・技術ブログ自動生成システム(Auto-Zenn-Pipeline)

## セットアップ

1. **GitHub リポジトリを private に設定する**
   - 手順: Settings → General → Danger Zone → Change visibility からリポジトリを private に変更します。
   - 会話の生ログに秘密や個人情報が混じるおそれがあるためです。Zenn との連携は private リポジトリでも利用可能です。
2. **Workflow permissions を設定する**
   - 手順: Settings → Actions → General → Workflow permissions で、Allow GitHub Actions to create and approve pull requests を有効にします。
3. **Repository secret を登録する**
   - 手順: Settings → Secrets and variables → Actions に、Repository secret として `ANTHROPIC_API_KEY` を登録します。
4. **Zenn とリポジトリを連携する**
   - Zenn のダッシュボードでこのリポジトリと連携し、デプロイするブランチを `main` に設定します。

> **注意**
> - 生成される記事は frontmatter が `published: true` になっています。
> - そのため、PR を `main` にマージした時点で Zenn に公開されます。
> - マージの前に、本文を必ず確かめてください。

## ハーネスとの契約

- ログのファイルの形式は、次の 3 つの JSON Schema に従います。
  - `schemas/chat_history.schema.json` (`chat_history.json`)
  - `schemas/git_context.schema.json` (`git_context.json`)
  - `schemas/adr.schema.json` (パイプラインが出力する `adr.json`)
- ログは、1 セッションごとに `raw_logs/<YYYY-MM-DD>_<session_id>/` に置き、`chat_history.json` と `git_context.json` を入れます。`<session_id>` に使える文字は英数字、`_`、`-` だけです。
- push 先は `main` にします。`main` に `raw_logs/**/chat_history.json` が push されると、`.github/workflows/01_generate_draft.yml` が自動で起動します。
- 1 セッションが記事 1 本になります。

## ローカルでの確認

前提として、環境変数 `ANTHROPIC_API_KEY` が必要です。

```bash
uv sync
uv run python scripts/generate_article.py --session raw_logs/<dir> --no-publish
```

`--no-publish` を付けたときの動作:
- ブランチ、タグ、PR は作りません。
- 生成物として `articles/<slug>.md` と `raw_logs/<dir>/adr.json` をローカルに書き出します。

確かめ終わったら、この 2 つのファイルを削除してください。残すと次の実行が「生成済み」で止まり、コミットすると本番に公開されてしまうためです。

## 再生成の手順

1. 生成された PR がまだ開いていれば閉じます。
2. リモートのブランチ `article/<session>` とタグ `draft/<slug>` を消します。
   - 例: `git push origin --delete article/<session>` および `git push origin --delete refs/tags/draft/<slug>`
   - `<slug>` は PR の本文の Tag の行で分かります。
3. `main` に `raw_logs/<session>/adr.json` が既にあるとき（前の PR をマージした場合）は、それを消して `main` に push します。
4. Actions → 「Generate draft」→ Run workflow を選び、`session_path` に `raw_logs/<session>` を入れて実行します (workflow_dispatch)。

補足: これらを消さずに実行すると、`adr.json`、ブランチ、タグのどれかが残っている場合に `AlreadyGeneratedError` で止まります。

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
