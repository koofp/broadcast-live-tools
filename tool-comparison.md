# B 站直播录制备选工具对比（子代理调研汇总）

> 调研方法：3 个子代理并行查证（BililiveRecorder / bililive-go / 综合），附来源 URL。更新 2026-08-19。

## 1. 对比表

| 维度 | **bilive**（当前在用） | **BililiveRecorder** | **bililive-go** | **biliLive-tools** |
|---|---|---|---|---|
| 录制 | ✅ 多房间/分段/断流合并 | ✅ 多路/断流修复/自动开场 | ✅ 多平台/多房间/断流重连 | ➖ 后处理为主(配录播姬) |
| 弹幕录制 | ✅ | ✅ | ✅ | ✅ 弹幕处理 |
| **弹幕渲染** | ✅ 内置(烧进视频) | ❌ 需外部 | ✅ ASS 烧录 | ✅ 压制 |
| **语音转文字(ASR)** | ✅ 内置(whisper local/groq)，但srt不保留 | ❌ | ❌ | ❌ |
| **AI 总结/切片标题** | ✅ MLLM(切片标题) / 无整场总结 | ❌ | ❌ | ❌ |
| 封面生成 | ✅ 图生图 | ❌ | ❌ | ❌ |
| **可扩展性** | 高(装饰器分发+submodule+SQLite队列+Docker) | 中(用户脚本API+.NET库) | 高(REST API+钩子+config) | 中 |
| 部署 | Docker/WSL(Python3.10) | Docker/WPF/CLI | 单Go二进制+Docker，低占用 | 桌面 |
| GPU | 可选(ASR本地需NVIDIA) | 无 | 无 | 无 |
| 维护 | 活跃(0.3.1=最新tag) | 活跃(~4790⭐,2026-08仍在提交) | 活跃 | 活跃 |
| 风控-352 | 已含buvid3+WBI补丁；账号级被风控仍会拦 | 有重试/熔断缓解,非根治(issue#661/#790) | 较稳(主平台) | — |

## 2. 关键结论（来源）
- **BililiveRecorder**：`BililiveRecorder/BililiveRecorder` 为官方(4790⭐,C#)，`ccf0515` 只是 fork。**纯录制**，无转写/字幕/AI；可扩展靠[用户脚本](https://rec.danmuji.org/reference/userscript/)。
- **bililive-go**：多平台、低占用、REST API + 录制完成钩子（可外接 ASR/LLM），但**无内置转写/AI**[README](https://github.com/bililive-go/bililive-go)、[API.md](https://github.com/bililive-go/bililive-go/blob/master/docs/API.md)。

## 3. 针对用户需求的最终推荐

> 需求 = 录制 + 语音转文字保留文字稿 + AI 整场总结 + 好扩展

**推荐组合：**
1. **录制底座**：bilive（当前主力，功能最全）**或** bililive-go/BililiveRecorder（如更看重稳定/低占用）
2. **语音转文字**：旁路脚本 `bilibili_transcribe.py`（本会话已交付，groq/local 双后端，输出保留 .srt）
3. **AI 总结**：同一脚本的 `--llm ollama|openai|groq` 输出 `.summary.md`

**单一工具结论**：没有一个开源工具"开箱即用地同时满足 录制+保留字幕+整场AI总结"。bilive 最接近（内置 ASR+切片标题），但 srt 烧进视频不保留、无整场总结——这正好由我们交付的旁路脚本补齐。**当前方案（bilive 录制 + 旁路脚本转写/总结）就是最优且全面的组合**。

> ⚠️ 无论选哪个，B 站直播都必须过风控（尤其 -352）：需**有效账号 + 住宅出口**，见 `bilive-runbook.md`。
