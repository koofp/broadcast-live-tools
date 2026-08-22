# bilive 排障与部署 Runbook（终版 · 实战复盘）

> 更新：2026-08-22 v2。经对抗性子代理评审后的最终架构。

---

## 0. 当前架构（已固化 ✅）

**本地存档模式**：只录制，摘除 scan/upload（保护原始文件、零投稿风险）。

- 镜像 `bilive-fixed:0.3.1` = 官方 0.3.1 + WBI修复版blrec + openai-whisper（源码 `bilive/Dockerfile.fixed`，已 git 提交 `1e5ae94`）
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
- 环境变量注意：用户级 OPENROUTER_API_KEY 对"已存在的父进程树"不生效，新开的计划任务/新终端可见；手动测试时内联 `$env:OPENROUTER_API_KEY=...`。
- 转写速度实测（small/int8，本机）：30 分钟段 ≈ 11 分钟（2.7x）；孤儿 flv 可直接转写。

## 5. 扩展资产

## 5.7 运维自动化套件（2026-08-22 上线，经子代理对抗评审）

| 组件 | 用途 | 触发 |
|---|---|---|
| `status.ps1` | 红黄绿监控：容器/进程/**fake-ip劫持指纹**/分段增长采样/磁盘可录天数/全房间积压；末尾输出 JSON | 手动（10秒） |
| `process_all.ps1` | 幂等批处理：mp4∪孤儿flv 全房间遍历→small转写→ox-alpha总结；FileStream崩溃安全锁(PID戳/2h强抢)；BelowNormal低优先级；日志 logs\pipeline\*.log(14天轮转)；失败自动进下轮 | **计划任务 bilive-pipeline 每30分钟** / 面板按钮 |
| `cleanup.ps1` | 归档清理：仅删「srt>1KB 且有summary 且超48h」的最旧段，删至200GB；与 process_all 同锁；删除留档 deleted.log | 磁盘<150GB 时手动 -Apply |
| `panel.py` | Web面板 127.0.0.1:9090：状态卡(容器/增长/磁盘/积压/DNS劫持)、跨房间分段表+徽章、单文件/全部处理按钮(409互斥)、提示词编辑器(校验{srt})、总结查看 | **计划任务 bilive-panel 登录自启** |
| 计划任务 | bilive-pipeline(MINUTE/30) ✅Ready；bilive-panel(ATLogOn指定用户) ✅Ready | — |

### 关键运维事实
- **Docker Desktop 自启已确认**（HKCU Run 键）；重启链路=登录→Docker自启→compose unless-stopped→计划任务，T5 演练通过（restart 后 0 分钟新 flv 出现）
- 磁盘 autonomy ≈3.5-4 天（~3GB/h）；<150GB 跑 `cleanup.ps1 -Apply`
- blrec `[space] recycle_records=false`：blrec 不删录像，cleanup.ps1 是唯一删除者
- `reserve_for_fixing=false`：MOOV 崩溃会删视频，重要场次前临时改 true
- 转写引擎权重：`models\faster-whisper-small\`（ModelScope 下载；HF/hf-mirror 本网络不可达）
- OpenRouter key 在用户环境变量 OPENROUTER_API_KEY（源码零硬编码）
- 已知坑：ps1 必须 UTF-8 BOM（WinPS 中文）；schtasks 内嵌引号会坏→用 Register-ScheduledTask

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
      （OpenRouter stealth/ox-alpha；⚠️推理模型：max_tokens≥16000 且 reasoning effort=low，
        否则 token 全被思考吃光 content=null；实测30min段 58s 出稿 24926 tokens）
```
实测成果（13-31 分段）：858 条字幕 srt + 结构化总结（识别出 BB vs Nigma 赛事、英雄/装备/金句全对）

### 模型配置（两个 LLM 通道）

**① OpenRouter（已验证可用）** —— OpenAI 兼容：
```python
url = "https://openrouter.ai/api/v1/chat/completions"
headers = {"Authorization": "Bearer sk-or-v1-...", "Content-Type": "application/json"}
body = {"model": "stealth/ox-alpha", "max_tokens": 2000,   # 推理模型！必须给足 max_tokens
        "messages": [{"role": "user", "content": prompt}]}
```
⚠️ ox-alpha 是推理模型：max_tokens 太小会被思考吃光导致空回复。

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
