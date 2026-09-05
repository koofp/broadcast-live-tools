# bilive 排障与部署 Runbook（v3 · 2026-08-23 终版）

> 更新：2026-08-24。全链路无人值守闭环验证通过。

> 📚 **文档地图**：项目入口看 [README.md](README.md)（架构/规范/路线图）；
> 变更史看 [CHANGELOG.md](CHANGELOG.md)（新变更记那里，本手册只保留当前状态）；
> 本文档 = **按症状排障的权威手册** + 配置速查 + 坑清单。
> ⚠️ 本手册小节号按追加时序生长（5.x 段落非连续），用 §编号 引用时以标题文字为准。

## 快速导航

| 你要做什么 | 去哪儿 |
|---|---|
| 看一眼系统状态 | 面板 http://127.0.0.1:9090/ → 仪表盘 |
| 看录制房间/直播状态 | 面板 → 录制 |
| 查看所有分段转写进度 | 面板 → 片段库 |
| 调度处理/查看日志/清理 | 面板 → 流水线 |
| 阅读 AI 总结 | 面板 → 总结库 |
| 改提示词/回滚/系统信息 | 面板 → 设置 |
| 手动处理一批分段 | `process_all.ps1`（或面板流水线页"入队全部"）|
| 检查磁盘/监控 | `status.ps1`（或面板仪表盘）|
| 改码后回归验证 | `.\verify.ps1`（30秒：py编译/ps解析/BOM/冒烟）|
| 端到端自测（不耗API） | `.\selftest.ps1`（一键六步断言：造视频→跑批→占位→聚类→报告→清理；方法详见 §5.98）|
| 场次聚合/整场总结 | `python session.py`（扫描+缓存）；`--room 8139918 --summarize [场次ID]`（LLM 场级总结）；`--title/--merge/--split` 纠错；详见 §5.99 |
| 元数据备份 | 每日 10:00 自动（bilive-backup）；手动 `.\backup_metadata.ps1`；详见 §5.100 |
| 主动告警 | `.\notify.ps1 -Title "..." -Text "..." [-Level bad]`（Windows Toast+notify.log，30分钟节流）|
| 清理旧分段 | 面板流水线页"归档预览"→"Apply"（或计划任务每日自动）|
| 生成全量复盘报告 | `python report_gen.py` → REPORT.md |
| 质量抽检某段 | `python qa_check.py <srt路径>` |
| 开面板 | 双击桌面「启动面板」（即项目根目录 `启动面板.cmd`）；2026-08-23 起改为手动启动，自启任务已禁用 |

---

## 0. 当前架构（已固化 ✅）

**本地存档模式**：只录制，摘除 scan/upload（保护原始文件、零投稿风险）。

- 镜像 `bilive-fixed:0.3.1` = 官方 0.3.1 + WBI修复版blrec + openai-whisper（源码 `bilive/Dockerfile.fixed`，已 git 提交 `(见git log)`）
  ⚠️ 镜像内的 openai-whisper 属历史遗留——**现役转写走宿主机 faster-whisper**（transcribe_host.py），
  容器内 whisper 仅在手动 `docker exec` 调试时使用；容器方案脚本 bilive_pipeline.ps1 已删除。
- 编排 `bilive-docker/docker-compose.yml`：
  - 控制台 **仅 127.0.0.1:22333**，密码 `Bil1veLocal2026`
  - 挂载覆盖 `/app/start.sh` → `start-local.sh`（record + tail 保 PID1；scan/upload 已摘除）
  - volumes: bilive.toml / settings.toml / Videos / logs
- 日常管理一律用 `docker compose up -d / restart`（在 bilive-docker 目录）

### 录制触发机制（务必知晓）
开播检测主要靠弹幕 WS 推送；兜底轮询每 600s 且启动后先睡满 600s。
实测修复后**启动即鉴权成功，~9 秒开录**；最坏情况等 ~10 分钟属正常。

## 0.5 根因定论

官方镜像 0.3.1（2025-04 构建）内置的 blrec **不带 WBI 签名**（容器内 api.py 中 wbi 相关=0 处），而 B 站已强制该接口签名 → 无签名必 `-352`。项目仓库 wheel 已于 aaf5c74（2025-07）修复但镜像从未重构建。诊断期间 Clash fake-ip 劫持（DNS 解析到 198.18.x.x）严重污染了对照实验——录 B 站必须退 Clash。

---

## 1. 修复步骤（已在当前容器执行并验证）

```bash
# 1) 把项目仓库里已修复的 wheel 装进容器（文件名必须保持原名）
docker cp bilive/wheel/blrec-2.0.0b4-py3-none-any.whl  bilive_docker:/tmp/
docker exec bilive_docker pip install --force-reinstall --no-deps -q /tmp/blrec-2.0.0b4-py3-none-any.whl

# 2) 验证签名代码就位（应输出 >=1）
docker exec bilive_docker grep -c w_rid /usr/local/lib/python3.10/site-packages/blrec/bili/api.py

# 3) 重启容器（restart 保留文件层；recreate 会丢！）
docker restart bilive_docker

# 4) 固化成新镜像（防重建丢失；--no-pause 避免卡住）
docker commit --no-pause bilive_docker bilive-fixed:0.3.1
```

之后若需重建容器，改用固化镜像：
```bash
docker run -itd --name bilive_docker --restart unless-stopped \
  -e RECORD_KEY=bilive2024 -p 22333:2233 \
  -v .../bilive.toml:/app/bilive.toml -v .../settings.toml:/app/settings.toml \
  -v .../Videos:/app/Videos -v .../logs:/app/logs \
  bilive-fixed:0.3.1
```

## 2. 成功验证记录（2026-08-22）

- 房间 `8139918`（在播）：容器重启后 ~90 秒内自动开录
- `/app/Videos/8139918/8139918_20260822-13-31-13.flv` —— 持续增长（h264 1280×720@30 + AAC）
- `.jsonl` 弹幕持续增长（鉴权生效）；`.xml` 为 0 属 blrec 缓冲行为，待观察/由 scan 兜底
- 另有历史佐证：8月20日曾在弹幕大部分失败的情况下录出 302MB flv（视频录制不依赖弹幕）

## 3. blrec 触发机制（源码级，务必知晓）

- 开播检测**主要靠弹幕 WS 推送**（LIVE 命令）；兜底轮询**每 600 秒一次且启动后先睡满 600s**
- 启动序列是"卡住等第一次弹幕鉴权成功"才级联启用监视器/录制器
- 因此：**容器重启后最多等 ~10 分钟才开始检测**，不是卡死

## 4. 配置速查

| 文件 | 要点 |
|---|---|
| `settings.toml` | `[[tasks]] room_id`；`[header] cookie` 推荐留空（匿名250画质即可）；路径不可改 |
| `bilive.toml` | `[asr] asr_method=none/api/deploy`；`tid` 必须填数字（空值会崩，官方模板默认为空是坑）；`[slice]/[cover]` 默认关 |
| 控制台 | http://localhost:22333 （RECORD_KEY=bilive2024），可热加房间 |

## 5.8 运行记录与已知修复

- **2026-08-22 晚**：批量管线自动消化积压中（计划任务首跑即触发）。深度检查发现并修复：summarize_host.py 重写时漏 `import urllib.request` → 14-08 段总结三连败；已修复并补跑成功。教训：**重写文件后必须 py_compile + 冒烟一次真实调用**。
- 环境变量注意：用户级 OPENROUTER_API_KEY 对"已存在的父进程树"不生效，新开的计划任务/新终端可见；手动测试时内联 `$env:OPENROUTER_API_KEY=...`。（2026-09-05 起首选设置页「AI 供应商」provider.json，面板改完即全链路生效，无进程树可见性问题）
- 转写速度实测（small/int8，本机）：30 分钟段 ≈ 11 分钟（2.7x）；孤儿 flv 可直接转写。

## 5.8 面板 v3（任务队列 + 录制页）

- **任务队列**取代"锁拒绝"模式：点击处理=高优先级入队插队；批量积压=低优先级补位；Worker 线程串行执行；queue.json 持久化，面板重启自动恢复未完成任务；流水线页有队列表格与"入队全部/清除已完成"
- **录制页 /recording**：房间卡（直播状态60s缓存/标题/在线/分段数/总大小/**正在录制实时增长**）+ 添加/移除房间（写 settings.toml + 重启容器二次确认）
- 修复：retry.txt 统一根目录；占位 srt（无语音）跳过 AI 总结（ps1 与 Worker 双侧）；worker 子进程 key 走注册表兜底；live_status int/str 键型；settings.toml 剥 BOM（PS5.1 Set-Content 引入的 BOM 会让 tomllib 崩——**写 toml 后务必无 BOM**）

## 5.9 面板 v3.1 修复与运行实录（2026-08-22 深夜）

- **UI v3 视觉**：渐变 Logo、图标芯片状态卡、辉光侧边栏、脉冲直播点、圆点徽章、定制滚动条
- **队列 Worker 实战验证**：主播连播 3h+ 期间自动接单；一次 NameError（main 漏 import subprocess）被 Worker 异常处理捕获并记录——修复后新分段 22-05-29 自动完成转写+总结
- **429 限流应对**：OpenRouter 免费档批量总结会触发 429 → 已加专项长退避（60/120/180s）；失败项落 retry.txt 下轮优先
- **锁自愈实测**：伪造 run.lock → 面板独占探测自动清除 ✓
- **统计口径修正**：积压=无srt 或 有srt无summary（10 分钟内活跃分段不计）；ChangeExtension 尾点坑已绕开（Substring 截断）
- 已知显示坑：PowerShell 表格对 <50KB 文件 MB 舍入显示 0——判断文件是否为空用精确字节数

## 5.95 面板 v3.2 修复实录（2026-08-23 深夜）

- **settings.toml CRCRLF 损坏（重大）**：`remove_room` 用 `read_bytes()+write_text()` 组合，Windows 换行二次翻译把全文件写成 `\r\r\n` → tomllib 崩（面板日志刷屏 TOMLDecodeError）、**容器重启后 blrec 也会崩**。已修复字节（修复前备份文件已于 2026-08-24 清理，损坏态可由本记录与 git 历史复原）；代码侧三重防护：写入统一走 `_write_settings()`（LF 落盘）、解析前归一化换行、读用 universal newlines。教训升级：**写 toml 必须 write_bytes+LF，读写两端都要防换行翻译**。
- **「正在录制」判定 v2**：旧逻辑只看"最新视频 mtime<600s"，被 blrec 后处理 remux 误触（分段结束后新写 mp4、中途仅几 MB → 卡片显示过 `15-23-59.mp4/10MB` 假红灯），且下播后红灯可残留 10 分钟。现以 B 站 live_status 为主：直播中+最新段≤15min 才亮灯；未开播绝不亮；状态未知沿用旧启发式。API 补齐 `newest_age_min` 字段（修"最新 - 分钟前"）。
- **页面跳转慢根因**：全部路由 `async def` 却在事件循环里跑阻塞子进程（status.ps1 全量 5~10s、ffprobe 容器 exec、B站 API 串行 6s×N），一个慢请求拖死所有页面；录制页还为拿一个容器字符串跑全量 status.ps1。修复：路由改同步 `def`（线程池）、status() 加 20s TTL 缓存、新增轻量 `container_status()`、live_status 并发拉取。实测页面冷/热均 **28~41ms**。
- **bilive-panel Result=1 根治**：任务动作 pythonw 直启 panel.py 且 WorkDir 为空；端口被占时 uvicorn 以退出码 1 失败（开机自启假故障）。修复：panel.py 先探测 9090，已有实例则 exit(0)；`Set-ScheduledTask` 补 WorkDir。验证 Start-ScheduledTask 拉起成功。
- **潜伏 bug**：pipeline_state 对 durations_get 按元组解包（实为 float），管线转写期间打开仪表盘必 500。已修。

## 5.96 全量代码审查与回归基建（2026-08-24 凌晨）

三路子代理评审（面板/运维脚本/宿主Python）+ 三视角辩论（SRE/数据完整性/极简主义）定案执行。

**修复的高危**
- panel Worker `_worker_env` 函数与模块变量同名 → env=收到函数对象，队列任务全瘫痪
- api_docker_restart kwargs 重复传 cwd → 重启容器按钮必 500
- cleanup 拿锁后从不写锁戳 → 长跑>2h 被 process_all 误判死锁强抢成双进程（已补 PID 戳）
- report_gen deleted.log 路径错（bilive-docker/logs→logs），archived 合并一直是死代码
- bilibili_transcribe ts() 对元组误调 replace 必崩 + 毫秒全丢
- Worker 子进程无超时（现转写1h/总结30min兜底）；pop_next_job 在拷贝上置状态致落盘仍 queued；
  acquire_run_lock 前置探测的 TOCTOU 窗口

**机制改进**
- retry.txt 双侧对账（process_all 末轮 + summarize_host 每次调用前）：剔除已成功条目+去重+清零删文件
- 锁心跳：process_all 每个文件处理前刷新锁 mtime，防长批次(>2h)被下一轮强抢
- 编码链统一 UTF8：process_all [Console]::OutputEncoding + 四个 py stdout.reconfigure
  （此前 GBK 日志乱码破坏面板进度正则 `(N条)`）
- cleanup v3：占位段(无语音)写入 summary 后即放行清理（护栏 mp4≥1MB），消除磁盘静默泄漏
- 红牌留痕：劫持+确认在录+停摆 三者同现时写 logs/pipeline/alert.log（2026-09-05 三态化，见 §5.7；无人看仪表盘也有据可查）
- 前端加固：App.esc 转义动态拼入 innerHTML 的外部数据；viewSum 改用服务端转义 HTML；
  settings 页 `$('save-state')` 死引用修复

**新基建**
- `verify.ps1` 一键回归：py编译/ps解析/ps1 BOM 存在性/关键产物/settings.toml 可解析/面板冒烟。
  **任何代码改动后必跑**（30 秒）。
- prompt.txt 物化入库（此前缺失时静默用内置默认值，不可见）
- 删除废弃脚本 bilive_pipeline.ps1（旧容器内 whisper 方案，零引用）

**新坑记录**
- ⚠️ 编辑工具重写 ps1 会剥掉 UTF-8 BOM → WinPS 中文乱码解析失败（今日踩两次；verify.ps1 已设 BOM 防线）
- FileStream.Write(byte[]) 单参重载在 pwsh7/.NET Core 不存在——跨 WinPS5.1/pwsh7 一律三参 Write(b,0,len)
- Task Scheduler 等待类检查脚本注意日期比较基准（'00:07' 会解析成今天凌晨而非明天）

## 5.97 房间数据隔离审计与按房间提示词（2026-08-24）

- **结论**：录制/字幕/总结/弹幕/质检产物全部落在 `Videos/<房间>/` 内，物理隔离 ✓；
  文件名天然含房间号（blrec path_template），无缓存键碰撞。跨房间共享物=
  锁/队列/retry/全局提示词（单机串行设计取舍，非缺陷）。
- **新机制：按房间总结提示词** —— 根目录存在 `prompt.<房间号>.txt` 则优先使用
  （必须含 `{srt}` 占位符），否则回退 `prompt.txt` → 内置默认。已为股票荐股房
  `1883948055` 建立财经向示例 `prompt.1883948055.txt`（可随时编辑/删除；
  游戏房 8139918 继续用全局版）。此前全局提示词硬编码偏向 DOTA2 术语，
  对股票直播的总结有系统性带偏。
- **settings.toml 已停止入库**（git rm --cached + ignore）：[header].cookie 现为空串
  无实际泄露，但换号流程（unlock-check）会填入真值——防患未然。
  ⚠️ **仓库托管于 GitHub（koofp/broadcast-live-tools），务必保持 Private**；换机迁移需手动拷贝该文件。
- 口径统一：片段库"录制中…"徽章改为"最近写入…"（mtime 启发式与录制页 v2 区分，
  主播下播后 10 分钟内不再误导）。
- **批处理两阶段化（隔离审计采纳）**：process_all 先规划（[plan] 待转写/待总结）再一次性调用
  transcribe_host / summarize_host——Whisper 权重**单次加载**，消除此前逐段 ~15s 重复加载；
  面板"当前处理"已适配批量模式。审计其余发现核实为不成立（文件名含房间号→缓存键不碰撞、
  keep.txt 可写 `房间号*模式` 表达房间限定）。
- 未来项备忘：接入失败通知渠道后可启用 qa_check 告警化；deleted.log 年增速极低暂不需轮转。
- **提示词 V3 与进化机制（2026-09-05）**：`prompt.14323359.txt` 词典扩至 ~80 词分组
  （国际拟称/市场资金/交易动作/板块标的/叙事话术），段级新增 **待验证预测** 与
  **新黑话捕获** 两个进化钩子小节；新建 `prompt_session.14323359.txt`（场级，
  session.py 自动优先于全局 prompt_session.txt）产出 **五分钟重点 + 十五分钟详读**
  双阅读版本 + 预测记分卡。场级输入升级为各段完整总结（原每段一句话，撑不起详读）。
  **结构保障**：think 模型对长结构化输出遵从度不稳定（实测同提示词一次 8/9 节、
  一次只剩详读），session.py `_assemble_sections` 做小节校验+缺失补全调用+规范序组装，
  必需小节清单 `SESSION_SECTIONS` 是下游提取契约（report_gen/总结库 one_liner）。
  **进化回路**：段级捕获新词/预测 → 场级汇总 → 用户确认后更新词典/画像 → 下场生效；
  **跨场记忆已落地**：`{last_scorecard}` 占位注入上一场记分卡，LLM 逐条对账
  （已证伪/已验证/待验证滚动记账，0827 场实测生效）；
  每积累 5-10 场可把全部场级总结喂给 LLM 重蒸馏"主播画像 v0"（prompt_session 内嵌）。

## 5.98 端到端自测（合成视频法 · 2026-08-24 固化）

**一键自测**：`.\selftest.ps1`（合成 3 个静音视频 → 两阶段批处理 → 占位分支 → 场次聚类 → REPORT → 自动清理，不耗 API）。
以下为方法说明与手工步骤（selftest.ps1 自动化了全部流程）。

不消耗 API 即可驱动"规划→批量转写→占位分支"全链路；总结路径需真实 API（步骤 5）。

```powershell
# 1) 造 8 秒静音测试视频（容器 ffmpeg 写入 _selftest 房间；非数字目录名不进面板房间卡）
New-Item -ItemType Directory -Force '.\bilive-docker\Videos\_selftest' | Out-Null
docker exec bilive_docker bash -c "ffmpeg -y -v error -f lavfi -i testsrc=size=320x240:rate=10 -f lavfi -i anullsrc=r=16000:cl=mono -shortest -t 8 -c:v libx264 -preset ultrafast -c:a aac /app/Videos/_selftest/selftest_001.mp4"

# 2) 回拨 mtime——绕过 10 分钟写入保护（防呆设计，不是 bug）
(Get-Item '.\bilive-docker\Videos\_selftest\selftest_001.mp4').LastWriteTime = (Get-Date).AddMinutes(-15)

# 3) 跑批。预期日志：[plan] 待转写 → [1/2] 批量转写 → [skip] 占位srt(无语音) → 结束；不触发总结
.\process_all.ps1

# 4) 验证产物：selftest_001.srt 含 [无语音内容]；selftest_001.summary.md 为占位说明

# 5) 总结路径（真实 API，约 20~60s）：造含真实内容的 srt →
#    前提：设置页「AI 供应商」已配 key（或 env/api_key.txt 有 legacy key）
#    python summarize_host.py <srt>   → 验证五段结构 → python qa_check.py <srt>

# 6) 清理现场
Remove-Item '.\bilive-docker\Videos\_selftest' -Recurse -Force
```

**本 SOP 首战战果**：揪出 WinPS GBK 占位符匹配 bug（§5.96 后仍潜伏，见 e7c151a）——
实测能抓到评审抓不到的编码类故障。

### 5.98.1 增补演练法（2026-09-05 实战沉淀）

**① 真实数据副本法（零删除）**：把真实房间的 srt 拷进 `Videos/_selftest/`（配 8s 假 mp4
占位命名），对副本跑 summarize/qa_check/场次总结——覆盖面与真数据一致且永不碰生产产物；
结束后删 `_selftest` + `python report_gen.py` 回基线。**禁止"删真实 summary 再重跑"**。

**② 锁三 drill**：PowerShell 持 run.lock 跑 `process_all.ps1` → exit 3（竞争退避）；
持锁加 `-Force` → 强抢日志（对"句柄未释放"的持锁者移除失败属正确行为）；面板处理中
taskkill → 重启 → 队列 running 自动 requeue → done（断电恢复）。
Python 侧锁 = `CreateFileW share=0` 真独占（services.py），与 PS FileStream 互认，
回归钉在 `tests/test_run_lock.py`。

**③ 测试记录落盘惯例**：演练结论（PASS/FAIL/覆盖缺口）记 CHANGELOG 对应日条目；
抓出的 bug → 修复 + tests/ 单测固化。未落盘的"已通过"视为无效。
**④ 评审-修复循环五步（固化）**：verify → 三视角子代理审查（架构师/怀疑者/执行者，
各自先读码再发言）→ P1 全修/P2 择修（每项写根因）→ 单测固化 → CHANGELOG+runbook 落盘。
**⑤ 备份集合必须 ⊇ 变更集合**：备份/快照只保护要动的对象，无参命令（如 session.py
  全房间重算）的变更面=全部房间，备份范围按命令实参算而非按计划文本算（2026-09-05 实训）。

**2026-09-05 执行快照**：selftest 六步 PASS；真实副本×3 过 DeepSeek-V4-Pro-0813-think
（qa_check PASS，单段 116~181s）；LLM 失败路径 401/不可达/429 退避 60s 符合预期；
面板七页 200；cleanup 冒烟（磁盘充足 + 锁重试实测）。**覆盖缺口**：真实开播检测延迟、
600s 轮询兜底、cleanup 全路径（磁盘充足不可达）、场次自然关闭（--force 等效覆盖）。
抓出并修复 3 bug：think 提额条件、tmp 竞争、run.lock 误删活锁（P1，见 CHANGELOG）。
录制腿（真实开播 5-10 分钟）待用户提供直播流后按评审定稿执行。

**测试管道坑**（伪影，非产品问题）：
- 内联 `python -c "…中文…"` 的字面量会被 GBK 传参搅碎 → 断言请写临时 .py 文件
- `python … | Select-Object -First N` 会掐杀上游进程（exit 变 -1）→ 看全量输出别截断
- 每次工具调用是独立进程，`$env:` 注入不跨调用存活 → 注入与执行放同一条命令

## 5.99 场次聚合与整场总结（2026-08-24 上线）

**概念**：把 30 分钟碎片段聚合成"场次"（一场直播），并生成场级二级总结——
把"录音笔"升级为"会议纪要"。三视角子代理研讨定案：sessions 是**派生视图**
（确定性聚类，幂等重算），sessions.json 只是缓存，不是持久事实。

**使用**：
```powershell
python session.py                                # 扫描全部房间，打印场次表并写缓存
python session.py --room 8139918 --summarize     # 该房间全部"已关闭且缺失/过期"场次的 LLM 场级总结
python session.py --room 8139918 --summarize 20260823_095436   # 指定场次
python session.py --title 8139918 20260823_095436 "TI决赛日"     # 命名（面板/报告显示用）
python session.py --merge 8139918 <ID_A> <ID_B>  # 误切分时手动合并
python session.py --split 8139918 <ID> <段文件名> # 在该段处强制切分
```

**机制**：
- 聚类阈值 50 分钟（相邻段开始时间差；真实数据校准：场内最大抖动 37min）
- 存储：`Videos/<房间>/_sessions/sessions.json` + `<场次ID>.summary.md`
- 场级总结头部带段指纹（`段数:名单哈希`）——新增/删除段后自动判 stale，重跑即刷新
- 触发条件：场次已关闭（末段>60min）且 ≥3 段；LLM 调用复用 429 长退避与原子写
- 触发方式：**仅 CLI**（`--room X --summarize`）——面板无场级总结入口，process_all
  每轮只重算缓存不生成场级总结（429 退避会占 run.lock，2026-09-05 定案）
- 段被 cleanup 归档后，场次元数据标 archived 保留（场级总结不因清理失效）
- 纠错持久化于 `_sessions/overrides.json`（boundaries/merged/titles），重算后仍生效
- 面板总结库：场次条目带徽章，阅读页含段级附录清单

**实测**（2026-08-24，TI 数据）：08-22 场 21 段（含 3 个旧命名段、37min 抖动正确合并）、
08-23 场 25 段（09:54–22:23 约 12.5 小时），两场真实 API 50 秒出稿，
时间线/要点/金句质量良好（Spirit 三冠叙事完整）。

## 5. 扩展资产

## 5.7 运维自动化套件（2026-08-22 上线，经子代理对抗评审）

| 组件 | 用途 | 触发 |
|---|---|---|
| `status.ps1` | 红黄绿监控：容器/进程/**fake-ip劫持三态**（blrec探针判在录，JSON 带 fakeip_state）/分段增长采样/磁盘可录天数/全房间积压；红牌=劫持+在录+停摆 | 面板每20s刷新线程 / 手动（10秒） |
| `process_all.ps1` | 幂等批处理：mp4∪孤儿flv 全房间遍历→small转写→AI总结（供应商走 provider.json）+ 场次缓存重算(session.py)；FileStream崩溃安全锁(PID戳/2h强抢)；BelowNormal低优先级；日志 logs\pipeline\*.log(14天轮转)；失败自动进下轮 | **计划任务 bilive-pipeline 每30分钟** / 面板按钮 |
| `cleanup.ps1` | 归档清理 v3：删「已处理(真实段srt>1KB / 占位段有summary)+超48h」最旧段至200GB；_trash 7天回滚+keep白名单+单次20段上限；与 process_all 同锁(带锁戳)；删除留档 deleted.log | **计划任务 bilive-cleanup 每2小时**（<150GB 才实际删除）/ 面板按钮 / 手动 -Apply |
| `panel.py` | Web面板 127.0.0.1:9090：四段流程条仪表盘、录制房间卡、跨房间分段表+徽章、任务队列+Worker、提示词编辑器、总结/字幕阅读 | **手动**：桌面「启动面板」一键拉起（计划任务已禁用；端口占用守卫防双开） |
| 计划任务 | bilive-pipeline(每30分钟) ✅Ready；bilive-cleanup(每2小时) ✅Ready；bilive-panel ❌已禁用(2026-08-23 用户选手动) | — |

### 关键运维事实
- **Docker Desktop 自启已确认**（HKCU Run 键）；重启链路=登录→Docker自启→compose unless-stopped→计划任务，T5 演练通过（restart 后 0 分钟新 flv 出现）
- 磁盘 autonomy ≈3.5-4 天（~3GB/h）；<150GB 跑 `cleanup.ps1 -Apply`
- blrec `[space] recycle_records=false`：blrec 不删录像，cleanup.ps1 是唯一删除者
- `reserve_for_fixing=false`：MOOV 崩溃会删视频，重要场次前临时改 true
- 转写引擎权重：`models\faster-whisper-small\`（ModelScope 下载；HF/hf-mirror 本网络不可达）
- LLM key 双链同源（provider_config.py，2026-09-05 起）：provider.json（设置页，含端点/模型）> env OPENROUTER_API_KEY（历史名，泛指 LLM key）> api_key.txt > 注册表；legacy key 时端点/模型同步回退 OpenRouter，绝不混搭（key 与端点不同源必 401）
- 已知坑：ps1 必须 UTF-8 BOM（WinPS 中文）；schtasks 内嵌引号会坏→用 Register-ScheduledTask
- LLM 中继 TLS EOF（`SSL: UNEXPECTED_EOF_WHILE_READING`）：Clash TUN(fake-ip) 下用户态无法绕过（DNS 全被劫持成 198.18.x.x），先看浏览器能否打开该站——能开=Clash 分流到坏节点，给域名加 DIRECT 规则或换节点；不能开=中继服务故障。对照：bilibili/baidu 正常+仅中继失败=分流问题（2026-09-05 实锤）

- `bilibili_transcribe.py`：旁路 srt 转写（groq/local）+ LLM 总结，幂等，支持目录递归
- `bilive-unlock-check.ps1`：换号/换出口一键验证+重配
- `tool-comparison.md`：备选工具对比（BililiveRecorder/bililive-go/bilive）

## 5.5 端到端管线（已全量验证 ✅ 2026-08-22）

**宿主机管线（推荐）**：
```
转写: python transcribe_host.py <视频...> --model models\faster-whisper-small
      （faster-whisper small/int8，实测30分钟音频→10.9分钟，2.74x实时）
权重来源: ModelScope（HF/hf-mirror 在本网络不可达），已存 models\faster-whisper-small\
总结: python summarize_host.py <srt>   → 同目录 .summary.md
      （供应商走 provider_config 双链：设置页 provider.json 优先（new-api 中继，OpenAI 兼容）；
        provider.json 无 key 时回退 legacy=OpenRouter stealth/ox-alpha。max_tokens≥16000，
        content=null 且 finish=length 时自动提上限重试；原 reasoning effort=low 参数已随供应商化移除）
```
实测成果（13-31 分段）：858 条字幕 srt + 结构化总结（识别出 BB vs Nigma 赛事、英雄/装备/金句全对）

### 模型配置（AI 供应商通道）

**主通道：设置页「AI 供应商」（provider.json，OpenAI 兼容）** —— 面板 → 设置页 →
Base URL / API Key / 模型列表 / 当前生效模型，保存即全链路生效（面板/批处理/场次总结同口径，
key 与端点/模型同源解析）。模型列表可点「⤓ 获取模型列表」在线拉取（`GET /v1/models`），
也可手动输入；测试按钮发最小 chat 请求回显延迟/错误；HTTP 200 但无 choices 或带 error
判失败。key 仅回显尾部 6 字符。当前生效：new-api 中继 + `DeepSeek-V4-Pro-0813-think`
（中继 /v1/models 共 21 个模型，已全量入 provider.json 下拉）。
⚠️ 耗时口径：测试按钮 2.9s 是 max_tokens=64 的 ping 指标；**真实长输入**（60KB 字幕）
think 模型先烧满 16K 再自动提额 64K，实测 116~181s/段（2026-09-05），排期按分钟计不要按秒计。

**legacy 通道（provider.json 无 key 时自动整套接管）** —— OpenRouter（机制保留；
⚠️ 默认模型 stealth/ox-alpha **2026-09-05 实锤已失效**：resolve() 会带 model_deprecated
标记 → 批处理日志打 `[warn]`、设置页红标提示；provider.json 损坏自愈（.json.bad+空骨架）
后会静默落 legacy，看到红标即去设置页重配 key）:
```python
url = "https://openrouter.ai/api/v1/chat/completions"
headers = {"Authorization": "Bearer sk-or-v1-...", "Content-Type": "application/json"}
body = {"model": "stealth/ox-alpha", "max_tokens": 2000,   # 推理模型！必须给足 max_tokens
        "messages": [{"role": "user", "content": prompt}]}
```
⚠️ ox-alpha 是推理模型：max_tokens 太小会被思考吃光导致空回复（call_llm 已有提上限重试兜底）。

**② 本地网关 http://localhost:8081（当前上游 502，待其恢复）**
- 容器内访问须用 `http://host.docker.internal:8081`（localhost 指向容器自身）
- 可用模型：MiniMax-M2.5/M2.7/M3、claude-fable-5、claude-haiku-4-5
- 当前状态：/v1/models 正常但 completions 返回 502 Upstream failed——需网关侧修复

### 完整管线复现命令
```bash
# 1) 从录播切样片
docker exec bilive_docker ffmpeg -y -ss 60 -t 120 -i /app/Videos/<room>/xxx.flv -c copy /tmp/clip.mp4
# 2) 转写（首次自动下载 tiny 权重 ~72MB）
docker exec bilive_docker whisper /tmp/clip.mp4 --model tiny --language zh --output_format srt --output_dir /tmp/
# 3) AI 总结
docker cp _summarize.py bilive_docker:/tmp/sum.py && docker exec bilive_docker python3 /tmp/sum.py all
```

### Agent 工具适配建议
- ox-alpha（推理+1M上下文+工具调用）：适合接入 agentic coding harness（Claude Code 类工具已在它的公开应用榜前列）；本会话所用 DeepSeek Harness 同类适配
- 本地 8081 网关模型：适合做批量后处理（总结/打标）——走 OpenAI 兼容协议即可，无需专用 agent 框架

## 6. 诚实勘误（此前的错误结论）

| 曾说 | 事实 |
|---|---|
| "账号太新被风控，换老账号" | ❌ 与账号无关；旧版 blrec 无签名必然 -352 |
| "家庭宽带 IP 被标记" | ❌ 出口正常；是 Clash fake-ip 污染了判断 |
| "手机热点能解决" | ❌ 无证据支持；未验证即建议，不该 |
| "镜像太老不能用" | 🟡 半对：镜像没跟上仓库修复，但重装 wheel 即可，无需弃用 |

## 9. 路径规范（重要教训）

**唯一正确路径**：`D:\CodeIDE\01-Code_item\...`（Code 后是**下划线**）。
2026-08-22 曾因命令中误写为 `01-Code-item`（连字符），Windows 视为新目录，blrec 在
`D:\CodeIDE\01-Code-item\...\bilive-docker\logs` 下产生了一套幽灵日志（已清理）。
所有脚本均已使用 $PSScriptRoot 相对路径，不会再发生；手工执行命令时注意核对。

## 10. 关键文件清单（当前工作区）

```
broadcast-live-tools/
├── README.md                  # 项目入口：架构图/日常三件事/工程规范/路线图
├── CHANGELOG.md               # 变更史（新变更记这里）
├── bilive-docker/             # 运行数据盘（勿整体删）：Videos 录像成果 / settings.toml(不入库) / compose 编排
├── bilive/                    # 上游开源代码（只读，独立 git）
├── panel/                     # Web 面板 v3.2（main.py 路由 / services.py 逻辑 / templates / static）
├── panel.py                   # 面板薄入口（端口占用守卫：已有实例则 exit 0）
├── 启动面板.cmd               # 桌面一键启动（检测 9090→拉起→开浏览器）；计划任务 bilive-panel 已禁用
├── process_all.ps1            # 每轮管线：场次重算→规划→批量转写(权重单次加载)→批量总结（计划任务每30分钟）
├── cleanup.ps1                # 归档清理 v3（计划任务 bilive-cleanup 每2小时；占位段已放行，mp4≥1MB 护栏）
├── status.ps1                 # 监控：容器/DNS劫持/磁盘/积压；红牌落盘 alert.log（30分钟节流）
├── verify.ps1                 # ⚡一键回归门禁（改码后必跑）：py编译/ps解析/BOM/产物/toml/面板冒烟
├── transcribe_host.py         # faster-whisper small 转写（宿主，2.7x 实时；支持多视频单次加载）
├── summarize_host.py          # AI 总结（供应商走 provider_config；429 长退避；按房间选提示词；retry对账）
├── provider_config.py         # AI 供应商统一配置（provider.json 读写 / 双链 key 解析 / 连通测试；设置页可配）
├── prompt.txt                 # 全局总结提示词（DOTA2 游戏向）
├── prompt.1883948055.txt      # 房间专属提示词示例（财经向；可按房间号复制扩展）
├── session.py                 # 场次聚合与整场总结（聚类/缓存/场级LLM总结/CLI纠错；详见 §5.99）
├── notify.ps1                 # 轻量通知：Windows Toast + notify.log（30分钟节流，四类事件已接线）
├── backup_metadata.ps1        # 元数据备份（bilive-backup 每日10:00；robocopy 增量不删历史）
├── selftest.ps1               # ⚡端到端自测一键化（合成静音视频驱动全链路六步断言，不耗API）
├── tests\                     # 纯逻辑回归单测（verify.ps1 调用）：merge_archived / provider_config
├── qa_check.py                # 质量验收（幻觉/重复/巨块/编码/五段结构/时间戳）
├── report_gen.py              # 全量复盘 REPORT.md（含已归档分段溯源）
├── bilive-runbook.md          # 本文档（权威排障手册）
├── start_batch.cmd            # 带 key 的批次启动器（gitignored，含 key 勿外传）
├── bilive-unlock-check.ps1    # 换号/换出口一键验证+重配（弹幕风控检测）
├── bilibili_transcribe.py     # 早期旁路转写工具（groq/local，留存备用）
├── tool-comparison.md         # 备选工具对比（历史决策依据）
├── REPORT.md                  # 生成数据：全量分段复盘表（report_gen.py 刷新，含场次视图）
├── models/                    # faster-whisper-small 权重（gitignored）
├── logs/                      # 流水线/面板/告警日志（gitignored，流水线14天轮转）
└── .gitignore                 # Videos/logs/models/settings.toml/provider.json(含key)/api_key.txt/.claude 等
```

## 11. 已注册的计划任务

| 任务名 | 触发 | 作用 |
|---|---|---|
| bilive-pipeline | 每 30 分钟 | 批量转写+总结兜底 |
| bilive-cleanup | 每 2 小时 | 磁盘 <150GB 时归档清理（含 keep 白名单/垃圾桶/锁重试）|
| bilive-backup | 每日 10:00 | 元数据备份：srt/summary/场次/配置 → 用户目录（robocopy 增量不删历史）|
| bilive-panel | **已禁用**（用户选择手动启动 2026-08-23）| 看板需要时双击桌面「启动面板」；录制/管线/清理不受影响；恢复自启：`Enable-ScheduledTask -TaskName bilive-panel` |

## 5.100 通知渠道与元数据备份（2026-08-24 P0 落地）

**通知**：`notify.ps1` = Windows Toast（落动作中心，错过可查）+ `logs/notify.log` 持久留痕，
同一内容 30 分钟节流，Toast 失败自动降级仅日志。已接线四类事件：
- status.ps1：劫持+在录+停摆 红牌（与 alert.log 同步）、磁盘不足 1 天
- process_all.ps1：批次结束有失败段（warn）
- cleanup.ps1：清理执行（info）、清理后空间仍不足 160GB（warn）
手动告警：`.\notify.ps1 -Title "..." -Text "..." [-Level info|warn|bad]`

**备份**：`backup_metadata.ps1` → `C:\Users\<用户>\bilive_backup`（与 D 盘数据盘物理隔离）。
robocopy 增量同步 `Videos` 下全部 `*.srt/*.summary.md`（**不含 /PURGE**——被 cleanup 清理的
分段其字幕/总结在备份端永久保留，历史资产不蒸发）+ settings.toml/prompt*/queue.json/keep.txt。
实测：101 文件 2.4MB。恢复方法：把备份目录内文件拷回对应位置即可（元数据与视频同名配对）。

## 12. 新对话启动提示（上下文切换）

> 把下面整段作为新对话的第一句话发给 AI，即可无缝接续本项目。

```
继续 bilive 直播录制系统（工作区 D:\CodeIDE\01-Code_item\01-Ai-item\ai-brower-tool\broadcast-live-tools）。
先读 README.md（架构/工程规范/路线图）和 bilive-runbook.md（排障权威：快速导航、根因定论、坑清单），
再看 REPORT.md 和 status.ps1 的当前状态，然后告诉我当前系统健康度和待办事项。
注意事项：面板是手动启动的（桌面「启动面板」图标）；改任何代码后必须跑 .\verify.ps1；
仓库托管于 GitHub（koofp/broadcast-live-tools），务必保持 Private。
```

如需继续推进某项任务，追加一句（例如）：
- 「继续优化面板 UI/逻辑」→ 参考 panel/ 下的 main.py、services.py、templates、static
- 「继续排查录制/转写问题」→ 参考 runbook §0.5 根因定论、§5.8 已知修复
- 「继续做归档/复盘」→ 参考 cleanup.ps1、report_gen.py
- 「做路线图里的 P0 项」→ README 路线图（通知渠道 / 元数据备份 / 补测试盲区）