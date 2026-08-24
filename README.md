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

## 分段生命周期

| 阶段 | 机制 |
|---|---|
| 切片 | 直播中每 **30 分钟**一段（`duration_limit=1800`）；下播/断流收尾成短段，录完自动 remux 为 mp4 |
| 结束判定 | blrec：断流 ≤10 分钟续录同段，超时收尾；面板判定 = B站 `live_status=1` 且最新段 15 分钟内有写入 |
| 场次聚合 | `python session.py` 按 50 分钟间隔把段聚类为"场次"，`--summarize` 生成整场二级总结（见 runbook §5.99） |
| 归档删除 | **唯一删除者** cleanup.ps1：磁盘 <150GB 触发；只删已处理+超 48h 段；`_trash` 7 天回滚；deleted.log 留档 |

## 日常三件事

| 想做什么 | 怎么做 |
|---|---|
| 看系统状态 | 双击桌面「启动面板」→ 仪表盘（四段流程条：录制→转写→总结→复盘） |
| 处理积压 | 自动（计划任务每30分钟）；手动：面板流水线页「处理全部积压」 |
| 看复盘 | 面板总结库，或 `python report_gen.py` 刷新 REPORT.md |
| 备份 | 每日 10:00 自动（bilive-backup → `C:\Users\<用户>\bilive_backup`）；手动 `.\backup_metadata.ps1` |

**面板不影响录制**：关掉面板，录制/转写/总结/清理照常全自动运转。

## 改代码必读（工程规范）

1. **任何改动后跑 `.\verify.ps1`**（30 秒：py编译/ps解析/BOM/产物/toml/面板冒烟）
2. ps1 必须 UTF-8 **带 BOM**（WinPS 中文前提）；编辑工具会剥 BOM，verify 会抓
3. 写 toml 一律 `write_bytes` + LF（`\r\r\n` 曾致面板与容器双崩）
4. WinPS 5.1 读无 BOM 文本默认按 GBK——正则匹配中文内容必须 `-Encoding UTF8`
5. **仓库托管于 GitHub（koofp/broadcast-live-tools），务必保持 Private**；settings.toml 不入库（换号会填真 cookie），历史已审计无密钥（2026-08-24）
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
- [x] **全链路重测验证** ✅ 2026-08-24：`.\selftest.ps1` 六步断言全过（候选扫描/批量转写/占位分支/场次聚类/REPORT/自动清理）；剩余=等 71003 首次真实开播终验
- [x] **通知渠道** ✅ 2026-08-24：Windows Toast（落动作中心）+ `logs/notify.log`，30 分钟节流；已接线 status 红牌/磁盘告急、process 失败、cleanup 执行与空间不足四类事件
- [x] **元数据备份** ✅ 2026-08-24：`backup_metadata.ps1` 计划任务 bilive-backup 每日 10:00 → `C:\Users\<用户>\bilive_backup`；robocopy 增量**永不删历史**（已清理段的字幕/总结永久保留在备份）
- [x] **补测试盲区**：批量总结路径真实执行测试 ✅ 2026-08-24（真实 API 17s 出稿，五段结构齐全，qa_check PASS；占位分支此前已实测）
- [ ] 用户决策项：房间 8139918 是否恢复自动录制（TI 已收官）；退出 Clash TUN

### P1 · 近期（有触发条件再做）
- [x] **场次聚合与整场总结** ✅ 2026-08-24：`python session.py` 按 50 分钟间隔聚类分段为场次（派生视图幂等重算）；`--summarize` 生成场级二级总结（一句话/时间线/要点/高光/金句 + 段级附录，指纹防过期）；REPORT 场次视图；总结库场次徽章；CLI 支持合并/拆分/命名纠错
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

## 现状快照（2026-08-24 · 数据重置后）

- **全部直播数据已清空重置**（14.8GB 已彻底删除；元数据备份在 C 盘保留）
- **全链路重测 PASS**（selftest.ps1 六步断言：候选扫描/两阶段批处理/占位分支/场次聚类/报告/清理）
- 系统回到零点：容器 Up、71003 在配置内（开播即录）、面板/流水线/备份/通知全链路就绪
- 剩余：等 71003 首次真实开播做终验
- 已知黄牌：Clash TUN fake-ip 运行中（不影响录制，勿切全局模式）
