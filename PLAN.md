# AgentState Guard — 实施计划

## 1. 架构概览

```
agentguard CLI
  ├── status       → 快速状态检查（Docker/容器/端口/版本/文件/漂移）
  ├── doctor       → 完整诊断（PASS/WARN/FAIL/SKIP + 原因）
  ├── checkpoint   → 记录快照（哈希、版本、配置、安全约束）
  ├── checkpoints  → 列出检查点
  ├── diff         → 比较当前与最近/指定检查点
  ├── restore      → 定点恢复单文件（白名单 + 原子写入）
  ├── report       → 生成 Markdown + JSON 报告
  └── update-state → 更新状态文档
```

## 2. 目录结构

```
agentstate-guard/
├── agentguard/              # 主包
│   ├── __init__.py
│   ├── __main__.py          # python -m agentguard 入口
│   ├── cli.py               # Click-free CLI (argparse)
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── status.py
│   │   ├── doctor.py
│   │   ├── checkpoint.py
│   │   ├── diff.py
│   │   ├── restore.py
│   │   ├── report.py
│   │   └── update_state.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py        # YAML 配置加载
│   │   ├── snapshot.py      # 快照创建/比较
│   │   ├── hasher.py        # 文件哈希
│   │   ├── sanitizer.py     # 密钥脱敏
│   │   ├── whitelist.py     # 路径白名单 + 穿越防护
│   │   ├── runner.py        # 命令执行（超时）
│   │   ├── docker.py        # Docker 状态检查
│   │   └── versions.py      # 版本检测
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── db.py            # SQLite 操作
│   │   └── snapshots.py     # 快照文件管理
│   ├── report/
│   │   ├── __init__.py
│   │   ├── markdown.py
│   │   └── json_reporter.py
│   └── docs/
│       ├── __init__.py
│       └── updater.py       # 文档更新（无 LLM）
├── config/
│   └── agentguard.yaml      # 配置文件
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_hasher.py
│   ├── test_snapshot.py
│   ├── test_sanitizer.py
│   ├── test_whitelist.py
│   ├── test_restore.py
│   ├── test_runner.py
│   ├── test_doctor.py
│   └── test_report.py
├── docs/                    # 状态文档输出目录
│   ├── CURRENT_STATE.md
│   ├── NEXT_STEPS.md
│   ├── DECISIONS.md
│   └── CHANGELOG.md
├── reports/                 # 报告输出目录
├── .agentguard/             # 数据目录（gitignore）
│   ├── state.db
│   └── snapshots/
├── .agentguard.example/     # 示例目录结构
├── .gitignore
├── pyproject.toml
├── PLAN.md
└── README.md
```

## 3. 数据流

```
status/doctor
    ↓
检查结果 (dict)
    ↓
checkpoint → 序列化 → SQLite + gzip 快照
    ↓
diff → 加载检查点 → 比较 → 脱敏输出
    ↓
restore → 验证白名单 → 备份 → 显示差异 → 确认 → 原子写入 → 验证哈希
```

## 4. 安全白名单（可恢复文件）

```
~/.claude/settings.json
~/.claude/settings.local.json
~/.claude/plugins/cache/superpowers/
~/.claude/skills/strategic-compact/SKILL.md
~/.claude/skills/context-compression/SKILL.md
~/.claude/skills/filesystem-context/SKILL.md
<project>/config/agentguard.yaml
```

禁止：/etc/、/var/lib/docker/、/mnt/c/、任何 socket 文件

## 5. 实现顺序

1. 项目骨架 + pyproject.toml + config
2. Core: hasher, sanitizer, whitelist, runner
3. Storage: SQLite DB, snapshot management
4. Commands: status, doctor, checkpoint, diff, restore, report, update-state
5. Tests (TDD: test before or alongside each module)
6. 验证 + 文档
