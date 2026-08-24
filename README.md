# bilive 直播录制系统

> 本地无人值守的 B 站直播录制 → 转写 → AI 总结 → 复盘管线。
> 单机 · 单用户 · 零成本运转（录制/转写/清理全自动，面板按需打开）。

## 架构总览

```
B站直播 ──► Docker 容器 bilive_docker (blrec, WBI修复版)
              │  录制 + 弹幕，30分钟自动分段，完成后 remux mp4
              ▼
   bilive-docker/Videos/<房间>/<房间>_时间戳.mp4
              │
   ┌──────────┴────────── 宿主机（计划任务每30分钟，run.lock 互斥）──────────┐
   │ process_all.ps1：两阶段批处理                                          │
   │   ① transcribe_host.py  faster-whisper small/int8 → .srt（模型单次加载）│
   │   ② summarize_host.py   OpenRouter ox-alpha → .summary.md              │
   │      （提示词按房间自动选择：prompt.<房间号>.txt > prompt.txt > 内置）  │
   └────────────────────────────────────────────────────────────────────────┘
              │
   cleanup.ps1（磁盘<150GB 触发）：48h保护 + _trash 7天回滚 + keep白名单 → 删至200GB
              │
   FastAPI 面板 http://127.0.0.1:9090（桌面「启动面板」手动拉起，看板+遥控+队列）
              │
   report_gen.py → REPORT.md 全量复盘（含已归档分段）
```

**数据隔离**：每个直播间的录像/弹幕/字幕/总结/质检产物全部独立存放于 `Videos/<房间>/`；
文件名天然含房间号，跨房间无键碰撞。

## 日常三件事

| 想做什么 | 怎么做 |
|---|---|
| 看系统状态 | 双击桌面「启动面板」→ 仪表盘（四段流程条：录制→转写→总结→复盘） |
| 处理积压 | 自动（计划任务每30分钟）；手动：面板流水线页「处理全部积压」 |
| 看复盘 | 面板总结库，或 `python report_gen.py` 刷新 REPORT.md |

**面板不影响录制**：关掉面板，录制/转写/总结/清理照常全自动运转。

## 改代码必读（工程规范）

1. **任何改动后跑 `.\verify.ps1`**（30 秒：py编译/ps解析/BOM/产物/toml/面板冒烟）
2. ps1 必须 UTF-8 **带 BOM**（WinPS 中文前提）；编辑工具会剥 BOM，verify 会抓
3. 写 toml 一律 `write_bytes` + LF（`\r\r\n` 曾致面板与容器双崩）
4. WinPS 5.1 读无 BOM 文本默认按 GBK——正则匹配中文内容必须 `-Encoding UTF8`
5. **本仓库仅限本地，永不 push 公网**（历史含敏感路径语义）
6. 跨 WinPS5.1/pwsh7 的 FileStream 写入一律三参 `Write(b,0,len)`

## 文档地图

| 文档 | 定位 |
|---|---|
| [bilive-runbook.md](bilive-runbook.md) | **权威排障手册**：架构、根因定论、按症状排查、配置速查、坑清单 |
| [CHANGELOG.md](CHANGELOG.md) | 变更史（新变更记录到这里，不再堆进 runbook） |
| [REPORT.md](REPORT.md) | 生成数据：全量分段复盘表（`python report_gen.py` 刷新） |
| [tool-comparison.md](tool-comparison.md) | 备选工具对比（历史决策依据） |

## 路线图

### P0 · 现在就值得做（低成本高价值）
- [ ] **通知渠道**：Windows Toast / ServerChan——接通后解锁 alert 红牌推送、失败清单告警、磁盘水位提醒（当前 alert.log 只落盘无人看）
- [ ] **元数据备份**：每日计划任务把 `*.srt + *.summary.md + settings + queue.json` 打包到异盘（录像单盘单副本，至少保住劳动成果）
- [ ] **补测试盲区**：批量总结路径的真实执行测试（需真人语音样本/TTS；占位分支已实测）
- [ ] 用户决策项：房间 8139918 是否恢复自动录制（TI 已收官）；退出 Clash TUN

### P1 · 近期（有触发条件再做）
- [ ] 多房间并行处理：触发条件=同时 ≥2 房间常态活跃且积压常超 10 段（当前单锁串行）
- [ ] 弹幕数据利用：总结附带高能弹幕时间戳（jsonl 已在录制，闲置中）
- [ ] cookie 时效巡检：每周自动跑 unlock-check 只读检测段
- [ ] 磁盘趋势曲线：仪表盘 7 天水位图（.status_cache 已在积累数据点）
- [ ] deleted.log 轮转：触发条件=文件 >1MB（当前年增速极低）

### P2 · 想法池
- [ ] 质量趋势看板（qa_check 数据积累后）
- [ ] Whisper 模型升级评估（medium / large-v3 + GPU）
- [ ] 移动端查看（Tailscale + 面板响应式）
- [ ] 每日复盘自动推送（REPORT 摘要）

## 现状快照（2026-08-24）

- 47 段全量处理完毕（转写+总结 100%），积压 0
- 面板 v3.2：页面 30ms 级、四段流程条、深链互通；手动启动（桌面图标）
- 已知黄牌：Clash TUN fake-ip 运行中（不影响录制，勿切全局模式）
- 已知测试盲区：批量总结路径（见 P0）
