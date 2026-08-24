# CHANGELOG

> 变更史。新变更记录到这里（按日期倒序），runbook 只保留"当前状态"不再堆时序日志。
> 格式参考 Keep a Changelog；提交号可 `git show <hash>` 查看全文。

## 2026-08-24 · 场次聚合功能上线

- **feat(session) `e8e5b29`**：场次聚合与整场总结 v1——`session.py` 把分段按 50 分钟
  间隔聚类为"场次"（派生视图幂等重算，双命名时间戳解析），场级二级总结
 （一句话/时间线/要点/高光/金句 + 段级附录，段指纹防过期），REPORT 场次视图，
  总结库场次徽章，CLI 合并/拆分/命名纠错。实测 TI 数据：08-22 场 21 段
 （37min 抖动正确合并）+ 08-23 场 25 段，真实 API 两场 50s 出稿。
  三视角子代理研讨（产品/架构/边界）定案：sessions=派生视图非持久事实。

## 2026-08-23 深夜 ~ 08-24 · 全量审查、测试、UI 与文档

- **docs `f7f7252`**：文档评审修正（8/10）——§5.7 旧表同步实况（cleanup 每2小时自动/
  panel 手动已禁用）；§0 标注容器内 whisper 为历史遗留；§10 清单补 7 项遗漏。
- **docs `83b2686`**：文档体系重组——新增 README（入口/规范/路线图）与 CHANGELOG；
  runbook 加文档地图、文件清单刷新、§12 引导词更新。
- **test `d812d44`**：关闭批量总结路径测试盲区——真实 API 执行 17s 出稿（五段结构齐全、
  内容断言通过、qa_check PASS）；README 路线图勾项。
- **test+fix `e7c151a`**：合成静音视频端到端自测，揪出潜伏 bug——
  WinPS5.1 `Get-Content` 默认 GBK 读无 BOM srt，占位符正则永假
 （占位段每轮误入总结队列，靠 python 层兜底潜伏；cleanup 占位检测同步失效）。
  三处显式 `-Encoding UTF8` 修复。另修流程条 `v>0` 字符串比较、viewSum 转义遗漏、
  segments 深链非法房间回退、`/api/files` 失败降级。
- **feat(ui) `11b6a2f`**：仪表盘四段流程条（录制→转写→总结→复盘，实时计数+深链）；
  侧边栏分组（监测/处理/产出/系统）；分段库 URL 深链；录制卡「分段」入口；
  修 summary_read pageInit 键错配（复制按钮从未生效）、tail_log 被 alert.log 抢占；
  alert 红牌 30 分钟节流。
- **feat(perf) `d65b027`**：process_all 两阶段化——规划→批量转写（Whisper 权重单次
  加载，消除逐段 ~15s 重载）→批量总结；面板"当前处理"适配批量模式。
- **feat(isolation) `1f5f9ea`**：房间隔离审计加固——REPORT archived 按「房间/文件名」
  溯源匹配；按房间总结提示词 `prompt.<房间号>.txt`（股票房财经向示例）；
  settings.toml 停止入库；分段库口径统一"最近写入"。

## 2026-08-23 · 全量审查与面板 v3.2

- **fix(panel) `1be0b1f`**：阻塞路由改线程池 + status TTL 缓存（页面秒级→30ms）；
  录制判定 v2（B站 live_status 为主，修 remux 误报红灯）；settings.toml CRCRLF
  损坏修复 + LF 写入防护；panel.py 端口守卫；面板改手动启动（桌面启动器）。
- **fix(ops/py) `d5907a9`**：锁心跳防长批强抢；空 srt 可重试；控制台 UTF8
 （修日志乱码破坏进度正则）；retry.txt 末轮对账；report_gen deleted.log 路径修正；
  多项 py 修复（429 末次不空睡/编码检查激活/ts() 崩溃等）。
- **fix(panel) `ae95e48`**：评审高危——Worker `_worker_env` 同名致队列全瘫痪；
  docker_restart 重复 cwd 必 500；子进程超时兜底；XSS 加固（App.esc）。
- **feat(ops) `e2108b0`**：cleanup 锁戳防双进程；占位段放行清理（mp4≥1MB 护栏）；
  红牌落盘 alert.log；新增 `verify.ps1` 一键回归；删除废弃 bilive_pipeline.ps1。

## 2026-08-22 · 运维自动化与面板 v3

- `9cad10e` 运维套件上线：status/process_all/cleanup/panel + 计划任务
- `a2be414` 面板 v2 多页面重构；`b3b896d` 面板 v3（录制页 + 持久任务队列）
- `572f856`/`d3288d2` runbook v3
- `61e7541` 面板 v3.1（Worker 异常捕获、429 长退避）
- `1dd05ff` stdout 重定向防 EINVAL、Worker 守护自动重启、电源永不离睡

## 2026-08-19~21 · 基线与风控攻坚

- `4df9823` 基线：本地存档管线资产
- `7db457d` 修 summarize 缺 import（批量总结曾全断）
- `5514e9d` 文档脱敏；`e59e23a` qa_check v2 / report_gen / cleanup v2 / 清理计划任务
- 根因定论（详见 runbook §0.5）：官方镜像 blrec 无 WBI 签名 → -352；
  Clash fake-ip 曾污染对照实验。修复版已固化 `bilive-fixed:0.3.1`。
