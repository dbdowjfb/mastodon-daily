# mastodon-daily

私有仓库，基于你的 Mastodon 收藏/favourite、转嘟/reblog、书签/bookmark，每天自动生成一份推荐 RSS。

## 原理
- 从 `favourites`、`bookmarks`、自己账号的 `boost` 拉取兴趣信号，构成画像。
- 从 `home`、`public?local=true`（本地实例）、`public?local=false`（联邦）拉取候选新帖，去重并过滤近期重复。
- 用 DeepSeek 视觉模型给帖子配图生成 alt 文本，和正文/标签一起交给本地 embedding（`jina-v5`）按画像排序，挑选 top-N。
- 非中文帖用 DeepSeek 批量翻译成简体中文。
- 生成 `public/feed.xml`、`public/index.html`。

## 数据来源
全部来自配置的 Mastodon 账号（当前 `dbdo@mas.to`）：

**兴趣信号（画像）**
- 收藏 `favourites` — 限 40
- 书签 `bookmarks` — 限 40
- 自己账号的转嘟 `boosts`（`/accounts/:id/statuses?exclude_replies=true` 里筛出 reblog）— 限 40

→ 按 id 去重、按时间倒序，取前 `profile.max_items`（30）条作为画像。

**候选（从中挑推荐）**
- `home` 关注流 — 限 40
- `public?local=true` 本站时间线 — 限 40
- `public?local=false` 联邦时间线 — 限 40

→ 跳过已在信号里出现的、超过 `window_hours`（48 小时）的、既无正文又无图片的，去重后截到 `max_candidates`（60）条。

各项上限/窗口见 `config.yaml` 的 `signals` / `candidates` 段；API 端点定义在 `mastodon_daily/masto.py`。

## 部署（hw 定时 → GitHub Actions → Netlify Deploy Preview）
链路：**hw 定时 → GitHub Actions 构建 → 把 `public/` 推到 `feed` 分支 → Netlify Deploy Preview 发布**。

- **RSS 地址**：https://deploy-preview-1--mastodon-daily-feed.netlify.app/feed.xml （即 `FEED_LINK`）。
- **定时**：`hw` 上 root crontab `0 18 * * *`（hw 是 US/Eastern，18:00 EDT = 22:00 UTC = 06:00 +08）执行 `/root/mastodon-daily-trigger.sh`，调用 GitHub API 触发 `daily.yml`（GitHub 自带 cron 不可靠，故用 hw）。注：进入冬令时后 18:00 EST = 23:00 UTC = 07:00 +08；若要全年精确 06:00 +08，把 hw 时区改为 UTC 并用 `0 22 * * *`。
- **构建**：`.github/workflows/daily.yml` 在 GitHub runner 上装 CPU torch + 依赖、跑测试、**每次全新构建**，然后把 `public/` 强推到 `feed` 分支。
- **发布**：仓库里有一个常开的 PR `feed → main`（#1），Netlify 会为每次推送生成 **Deploy Preview**（免费、无限），不消耗生产部署积分。
- **手动触发**：`ssh hw /root/mastodon-daily-trigger.sh`，或在 GitHub 上对该 workflow 点 Run workflow。
- 备注：Netlify 生产部署因 Free 额度用尽被暂停；若想换回生产/分支域名（`feed--mastodon-daily-feed.netlify.app`），在 Netlify UI 启用 “Branch deploys → 添加 `feed`”，再把 `FEED_LINK` 改过去即可。

## 本地测试
```bash
export MASTODON_BASE_URL=...
export MASTODON_TOKEN=...
export DEEPSEEK_API_KEY=...
mkdir -p public
python -m mastodon_daily.main
cat public/feed.xml | head -5
```

## 注意
- 排序用本地 embedding（免费、无需 API）；`sentence-transformers` 不可用时自动回退到关键词+图片启发式。
- 图片不再用 CLIP 做图-图相似度，而是先用视觉模型转成 alt 文本再走文本排序（见 `config.yaml` 的 `caption` 段，`reranker.image.enabled: false`）。
- 翻译对失败会重试并按批对半拆分；日志里可见 `translate: n/total succeeded`。
- DeepSeek 有时把内容放在 `reasoning_content` 而非 `content`，解析时两者都看（取第一个 `{ ... }`）。
