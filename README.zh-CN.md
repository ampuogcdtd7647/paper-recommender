# Codex 论文推荐 Skill

**其他语言：[English](README.md)**

一个可复用、可开源的 Codex Skill，用于个性化论文和技术材料推荐。Codex 会根据私有推荐画像生成检索词，通过网页搜索或论文 API 收集候选材料，再使用一个无第三方依赖的 Python 脚本，对相关性、项目匹配度、来源质量、时间新颖度、可实践性、历史去重和主题多样性进行打分。

## 它能做什么

- 提供通用默认画像，零人工配置即可开始使用。
- 根据长期兴趣和当前项目生成结构化搜索 query。
- 推荐论文、报告、工程实践文章、数据集和教程。
- 使用透明打分，而不是无法解释的“AI 觉得好”。
- 从用户选择或拒绝的材料中学习偏好。
- 可选开启本地项目扫描，但必须使用明确的文件夹授权列表。
- 将可复用 skill 源码与用户私有状态分离。
- 画像更新可以写入审计日志，方便追踪变化原因。

## 推荐信号

排序算法综合以下因素：

- BM25 风格的词法相关性
- 当前项目匹配度
- 来源类型与论文 / 报告 / 工程博客质量
- 可配置的时间衰减和新颖度
- 可实践性：代码、数据集、benchmark、系统设计、部署细节
- 用户喜欢或拒绝的话题反馈
- 阅读历史去重
- MMR 风格的主题多样性控制

完整打分模型见 [`references/algorithm.md`](references/algorithm.md)。

## 安装

把仓库克隆到 Codex skills 目录：

```bash
git clone https://github.com/ampuogcdtd7647/paper-recommender.git \
  "$HOME/.codex/skills/paper-recommender"
```

然后重启或刷新 Codex，让新 skill 被识别。

## 快速开始

### 1. 创建通用私有画像

```bash
mkdir -p ~/.codex/paper-recommender

python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  init --output ~/.codex/paper-recommender/profile.json
```

第一次运行不需要手动编辑画像。

### 2. 生成搜索 query

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/recommend.py" \
  build-queries --profile ~/.codex/paper-recommender/profile.json
```

然后让 Codex 按优先级搜索这些 query，并把确认过的结果保存到 `candidates.json`。

### 3. 给候选材料排序

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/recommend.py" \
  rank \
  --profile ~/.codex/paper-recommender/profile.json \
  --candidates candidates.json \
  --top 5 \
  --explain
```

输出会区分已选材料、未选材料、重复项和校验警告。

## 在聊天窗口中完成配置

用户可以完全通过和 Codex 对话完成配置，不需要手动编辑 JSON。

一次典型的配置对话只需要了解：

1. 长期阅读兴趣。
2. 当前项目或目标，如果希望推荐和项目相关。
3. 明确允许扫描的文件夹，如果希望开启本地项目发现。

Codex 会把用户回答整理成临时私有 answers 文件，并先预览结果：

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" configure \
  --profile ~/.codex/paper-recommender/profile.json \
  --config ~/.codex/paper-recommender/scan.json \
  --answers /absolute/path/to/answers.json
```

用户确认之前不会写入任何配置。确认后再加 `--write`，也可以加 `--audit`。用户也始终可以选择通用画像，并且完全不开启本地扫描。

answers 文件可以新增授权目录，也可以用 `remove_roots` 移除目录，或用 `replace_roots: true` 加完整 `roots` 列表整体替换授权范围。用户可以在聊天中完成授权和撤销授权。

## 渐进式个性化

画像一开始是通用的，之后可以通过明确、可审计的信号逐渐个性化。

### 查询允许扫描哪些文件夹

用户可以在对话框里直接问：

> 你可以扫描我的哪些文件夹？

Codex 会运行：

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  list-roots --config ~/.codex/paper-recommender/scan.json
```

如果还没有配置，答案会是“当前没有任何授权目录”。Skill 不会把当前目录、home 目录，或用户只是提到过的路径，自动当成扫描授权。

### 创建扫描授权列表

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  init-scan \
  --output ~/.codex/paper-recommender/scan.json \
  --root ~/Documents \
  --root ~/Desktop
```

### 预览项目发现结果

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  refresh \
  --profile ~/.codex/paper-recommender/profile.json \
  --config ~/.codex/paper-recommender/scan.json
```

刷新时可以使用私有项目 manifest。它保存证据文件的路径、大小、修改时间和哈希，
让未变化的项目复用已有画像，只重新提取新增或变化的项目：

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  refresh \
  --profile ~/.codex/paper-recommender/profile.json \
  --manifest ~/.codex/paper-recommender/project-manifest.json \
  --config ~/.codex/paper-recommender/scan.json \
  --write \
  --audit ~/.codex/paper-recommender/profile-audit.jsonl
```

`project-manifest.json` 是本地机器状态，不应提交到 Skill 源码仓库。刷新命令会输出
新增、变化、未变化和 stale 项目数量，以及 `recommendation_needed`，调度器可以据此
决定是否启动论文检索。详细字段见 [`references/manifest.md`](references/manifest.md)。

先预览，确认后才加 `--write` 更新私有画像，也可以同时写入审计日志：

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  refresh \
  --profile ~/.codex/paper-recommender/profile.json \
  --config ~/.codex/paper-recommender/scan.json \
  --write \
  --audit ~/.codex/paper-recommender/profile-audit.jsonl
```

扫描器只保存项目根目录、证据文件路径、修改时间和提取出的关键词，不会把原始文档内容复制进画像。

### 从阅读反馈学习

排序时使用 `--record-selection`，被选中的论文会写入私有 history。之后可以把这些反馈合并进画像：

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  learn-feedback \
  --profile ~/.codex/paper-recommender/profile.json \
  --history ~/.codex/paper-recommender/history.json \
  --write \
  --audit ~/.codex/paper-recommender/profile-audit.jsonl
```

这里刻意采用浅层学习：只根据明确的历史记录更新喜欢和不喜欢的话题，不构建不透明的用户模型。

## 隐私与安全边界

- 本地项目扫描必须显式开启。
- 只有出现在授权列表里的目录，或者当次命令明确传入的目录，才允许扫描。
- 扫描器会跳过常见依赖、构建、缓存、版本控制和临时目录。
- `.env`、credential、token、password、API key、private key 等敏感路径会被跳过。
- 只读取配置允许的文本格式。
- 私有画像、项目 manifest、历史、扫描配置和审计日志应保存在仓库外。
- 可复用 skill 源码不包含用户专属路径。
- 日常个性化更新的是私有画像，不修改 skill 源码，也不修改自动化任务 prompt。

更多细节见 [`references/profile-maintenance.md`](references/profile-maintenance.md)。

## 设计边界

Python 脚本刻意不做网络请求。候选材料发现由 Codex、其他 agent，或你自己的集成脚本完成。这让 skill 可以兼容 arXiv、OpenReview、Semantic Scholar、PubMed、Google Scholar 导出数据，或普通网页搜索。

## 仓库结构

```text
SKILL.md                         Codex skill 指令
README.md                        英文文档
README.zh-CN.md                  中文文档
scripts/recommend.py             query 生成与推荐排序
scripts/profile_manager.py       画像初始化与渐进式维护
references/                      格式、manifest、算法和维护设计文档
assets/                          通用、manifest 与示例配置
tests/                           离线集成测试
```

## 开发测试

```bash
python3 -m unittest discover -s tests -v
```

## License

以 [MIT License](LICENSE) 发布。
