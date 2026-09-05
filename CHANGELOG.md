# CHANGELOG

> 变更史。新变更记录到这里（按日期倒序），runbook 只保留"当前状态"不再堆时序日志。
> 格式参考 Keep a Changelog；提交号可 `git show <hash>` 查看全文。

## 2026-09-05 · 二轮审查（计划闭环四提交）+ 语义/边界修复

三视角审查（代码/计划复盘/架构）结论：0×P0/P1（六提交核心语义经怀疑式排除均安全）；
修复 3×P2 + 8×P3：
- **fix(race) session.py save_json tmp 加 PID**：场次缓存现在被 计划任务/面板 toggle/手动
  三方拉起（无统一互斥），固定名 tmp 双进程互写会落盘垃圾 JSON（对齐 summarize_host
  已定案的 PID 后缀模式）。
- **fix(data) merge_archived 整场归档重建**：整场段全被 cleanup 后该场从缓存蒸发，
  违背 §5.99"元数据不蒸发"承诺；且场次重算自动化后暴露面上升。现以纯归档身份重建
  （fingerprint 沿用旧值，场级总结不误判 stale），单测固化（幂等四场景）。
- **fix(ux) ignored 场级不再覆盖段级**：忽略场级总结=回到段级视图（语义定案）。
- **fix(guard) --summarize 数字参数守卫**：错误用法从 exit 0 静默空转改为 exit 2 + 指引。
- **refactor(panel) summaries_list 复用 sessions_index**：sessions.json 解析点
  O(items)→O(rooms) 单次读取，失败语义统一留痕；ignored/id 缺失防御入覆盖集。
- **feat(status) unknown 盲区分支**：劫持+blrec 不可知+停摆 → 黄牌留痕 + warn 通知，
  永不静默绿（评审 B3：当日实锤签名里 API 是活的，但该组合仍可能发生）。
- **fix(verify) /settings 冒烟匹配串**：'AI' 对 `<main class="main">` 恒真无鉴别力 →
  'pv-base'；补仪表盘页与 /api/status fakeip_state 字段探测。
- **refactor(provider) 内部调用统一 fetch**（_fetch 仅作兼容别名，消除测试/插桩双名陷阱）。
- **docs**：runbook 五处旧语义同步（§5.96/§5.7 两行/§5.100/§10/§5.99 触发方式）；
  §5.98.1 固化"评审-修复循环五步"与"备份集合⊇变更集合"教训；README 决策项补全
  （api_key.txt/1937830735）、段数口径 25→27（含 flv 孤儿段）。

## 2026-09-05 · 计划评审闭环：总结库可见性修复 + 场次自动化 + 告警三态化

三视角评审（架构师/怀疑者/执行者）定稿执行，实锤并修正 v1 计划的三处假设错误：
① `--summarize <房间号>` 文档用法静默空转（session.py:419 拿房间号比场次ID 永假，
`--summarize X <sid>` 直接 exit 2）——正确形式 `--room X --summarize`，四处文档勘误；
② "重算即可恢复可见性"不成立——隐藏规则按房间级触发，必须先改规则；
③ "末尾挂钩子"永不执行——process_all 多个提前 exit，钩子必须置于 exit 分支前。

- **fix(panel) 总结库覆盖式隐藏**：房间级隐藏（同房间有任意场级总结就吞全部段级）
  收敛为"有场级 summary 文件的场次所列段"精确隐藏；sessions.json 缺失/损坏时
  fail-open 全部可见并打日志。实锤场景：14323359 因 sessions.json stale（08-26）
  导致 15 条段级总结整库隐身。测试先行（先红后绿）`tests/test_summaries_list.py`
  （4 房间夹具：全覆盖/部分覆盖/无 _sessions/有场无缓存），verify.ps1 接入。
- **fix(session) 场次数据补齐**：14323359 重算为 4 场×5 段，补 3 场场级总结
  （首场指纹一致自动 skip）；总结库 8→11 条，08-27~09-01 内容重新可见。
- **feat(status) 告警三态化**：红牌原条件 `fakeip AND stall` 在"无直播"时必然误报
  （当日 notify.log 实录 10 条 BAD）。新增 blrec 本地 API 录制探针（零外网依赖，4s 超时，
  PS5.1 哈希键引号/`@()` 包裹/显式超时三陷阱规避），`fakeip_state=recording|idle|unknown`
  落 JSON 透传面板；红牌收敛为 **劫持+确认在录+写入停滞** 三者同现（当日真实故障签名）；
  idle 时停摆改黄牌"下播待机"。实测：同一数据态从 3 连红牌变为 `ok:true issues:[]`。
  dashboard 双消费点（Jinja 首绘 + JS 刷新）同步三态，带旧字段回退。
- **feat(pipeline) 场次重算自动化**：process_all.ps1 每轮（非 `-One`）在 exit 分支前
  重算全部房间 sessions.json——场次缓存永不再 stale；纯重算无 LLM（429 长退避占
  run.lock 风险排除），场级总结仍由面板/手动触发。
- **docs**：README 现状快照刷新（09-05：3 房间 25 段/供应商架构/Clash 已退出）；
  路线图勾选终验离线部分与可见性修复；session.py/runbook `--summarize` 用法勘误。

## 2026-09-05 · 三线审查（模型列表/_fetch/测试修复四提交）+ 遗漏修复

- **fix(P1) 测试隔离**：tests/test_run_lock.py 直接操作真实仓库根 run.lock——计划任务持锁
  时 verify 必假失败，且测试会清掉运维现场的陈旧锁。改 monkeypatch `services.LOCK` 到
  临时目录，产品代码零改动全隔离。
- **fix(P1) legacy 运行时陷阱**：provider.json 损坏自愈（.json.bad+空骨架）或新 clone 场景
  会静默落到 legacy 链 → 已失效的 ox 模型 → 总结全失败无提示（队列 j1-j10 failed 残留即
  真实案例，已清理）。修复：resolve() 带 model_deprecated 标记 → call_llm 打
  `[warn]` 指引（进程内一次）+ 设置页红标。
- **fix(P1→P2) 超时分类/锁自愈/直连记忆**：① urlopen 把连接超时包进 URLError(reason)，
  `isinstance(TimeoutError)` 判定落空 → 解包 reason 判定 + 单测；② acquire 陈旧锁自愈
  （面板被杀遗留锁文件不再让 Worker 空转）+ Worker defer 2s 退避（防紧循环刷 queue.json）；
  ③ _HOST_DIRECT 直连失败即清除记忆（防一次性抖动永久锁死直连）。
- **fix(P2/P3)**：DEFAULT_MODEL 播种占位改为实盘存在的模型 ID（原 "1M" 变体是幽灵模型，
  provider.json 丢失场景必失败）；list_models 兼容 Ollama 风格 `{"models":[...]}`、
  无法识别结构报顶层键名（不再误称"中继异常"）；resolve_models_url 防 /models 双拼；
  排障提示截断 200→400（结论段不再被砍）；_fetch 转正公共名（留 _fetch 别名）；
  services 的 B站 opener 改名 _BILI_OPENER（与 LLM 回退 opener 消歧义）；
  WriteFile/CloseHandle 补 argtypes + use_last_error；.gitignore 补 `*.tmp*`；
  verify 面板冒烟补 /settings 页渲染探测（此前 Jinja 500 探不到）；settings 页拉模型
  切换 pvActive 时明示（防无意覆盖生效模型）。
- **docs**：runbook §5.5 修正耗时口径（2.9s 是 ping，长输入 116~181s/段）+ legacy 陷阱
  运行时警示说明；§5.98 增补 5.98.1（真实数据副本法/锁三 drill/测试记录落盘惯例 +
  09-05 执行快照与覆盖缺口）。

## 2026-09-05 · 全链路模拟测试执行 + 三个实测抓出的修复

- **test**：按评审定稿执行可自治部分（子代理对抗评审原方案后定稿：真实数据零删除全走
  `_selftest` 副本、L4 并入主流程、第三方房间录制否决）。执行结果——selftest 六步 PASS；
  3 段真实 srt 副本经 DeepSeek-V4-Pro-0813-think 出稿（五段结构齐全）+ qa_check PASS；
  场级总结带指纹产出；LLM 失败路径 401/不可达/429 退避（60s 精确）全符合预期；
  面板七页全过；cleanup 冒烟顺带实测锁重试；锁竞争 exit3/-Force 强抢/断电恢复 PASS。
  覆盖缺口如实记录：真实开播检测延迟、600s 轮询兜底、cleanup 全路径（磁盘充足不可达）、
  场次自然关闭（用 --force 等效覆盖）。
- **fix(llm) 提额条件**：think 模型经 new-api 中继不回传 `reasoning_content` 字段，
  思考烧光 max_tokens 后 content=null+finish=length——旧条件 `not text and reasoning
  and fr=="length"` 永不触发，实测一段 4 连败各烧 16K token。改为仅凭 `fr=="length"`
  即提额重试（64K）。
- **fix(race) 原子写 tmp 加 PID**：手动运行与计划任务无 run.lock 互斥，同名
  `.tmp` 双进程互相覆盖（实测 16-30-00 段 process_all 与手动总结真实重叠，侥幸错开）。
  summarize_host / transcribe_host 的 tmp 改 `name.tmp<pid>`。
- **fix(lock) run.lock 真独占（P1）**：断电恢复测试抓出——acquire 用 CRT `open("x")`
  （共享全允许），lock_info 探测用 `open("r+b")` 能打开活锁文件 → 把 Worker 活锁当
  陈旧锁删除 → 互斥失效 + release unlink 撞 delete-pending 抛 PermissionError 误标
  任务 failed。Python 侧改 `CreateFileW` share=0 真独占（与 process_all.ps1 的
  FileStream None 共享互认），acquire/探测/释放三处收口；新增 tests/test_run_lock.py
  （verify.ps1 接入）。跨语言互斥实测：Python 持锁 → process_all exit 3 ✓。
- 另：_fetch 超时与 TLS 中断的排障提示分流（超时=中继慢，不再误导去查 Clash）。

## 2026-09-05 · LLM 请求层统一 _fetch（直连回退 + Clash TUN 排障提示）

- **fix(provider)**：设置页「测试当前模型」报 `SSLEOFError`——现场取证定位：Clash TUN
  fake-ip 劫持全部 DNS（223.5.5.5 也返回 198.18.x.x，用户态无法绕过），Clash 本身健康
  （bilibili 148ms/baidu 119ms），唯独 new-api.abrdns.com 被分流到故障去向（节点/规则问题，
  30 分钟前同路径实测正常）。代码侧修复：provider_config 新增统一请求层 `_fetch`——
  默认 opener（读系统代理）网络级失败 → 无代理直连重试一次（纯系统代理环境下 Clash 挂掉
  可自愈，主机级记忆避免重复尝试；HTTPError=已拿到服务端响应不重试）；两级全败抛出的错误
  附 Clash 分流排障提示（fake-ip 特征/加 DIRECT 规则/换节点/浏览器判活），设置页测试按钮、
  summarize_host._chat、list_models 三处收口同口径。TUN 环境下仍无法绕过（需用户改 Clash），
  但报错从裸异常变为可诊断。

## 2026-09-05 · 模型列表在线获取 + 切换 DeepSeek-V4-Pro-0813-think（ox 失效）

- **feat(provider)**：设置页新增「⤓ 获取模型列表」按钮——`POST /api/provider/models` 走
  `GET <base>/v1/models`（OpenAI 兼容，`resolve_models_url` 与 chat 端点同一套填法兼容），
  拉到列表自动去重排序填充模型下拉（失败仍可手动输入）。配置与生效：
  new-api 中继 + 全量 21 个模型入 provider.json，当前生效 `DeepSeek-V4-Pro-0813-think`
  （实测 2.9s finish=stop 回复 pong）。
- **deprecate**：stealth/ox-alpha 实锤失效（中继 /v1/models 无任何 ox 模型）——legacy
  双链机制保留作兜底，但默认 legacy 模型已不可用，应把 key 配进 provider.json
  （runbook §5.5 已注记）。
- **test**：test_provider_config.py 补 resolve_models_url 五种填法 + list_models
  去重排序/200+error/非 JSON/空 key 分支。

## 2026-09-05 · AI 供应商设置页（provider.json）+ 全链路口径统一（经三线审查修复）

- **feat(provider)**：设置页新增「AI 供应商」卡片——Base URL / API Key（仅尾部回显）/ 模型列表
  增删 / 当前生效模型 / 连通测试，配置落 `provider.json`（gitignored，绝不入库）。新增
  `provider_config.py` 统一层与 `GET/POST /api/provider`、`POST /api/provider/test` 端点；
  summarize_host / session / 面板三处重复的 get_api_key 收敛为同一 `resolve()`。
  key 解析**双链同源**：provider.json 有 key → 整套走 provider.json（含端点/模型）；
  否则 legacy 整套接管（env OPENROUTER_API_KEY > api_key.txt > 注册表 + OpenRouter
  stealth/ox-alpha）——key 与端点绝不混搭（OpenRouter key 打到 new-api 中继必 401）。
  summarize_host 的 call_oxalpha 重构为通用 `call_llm()`（自定义 model/chat_url/max_tokens；
  think 模型 content=null 且 finish=length 时自动提上限重试；原 `reasoning effort=low`
  参数随供应商化移除）。
- **fix(review) 三线并行审查（代码/计划复盘/架构）后修复**：
  ① `/api/provider` 入口严检——models 非字符串元素/空列表 → 400（原会 500 AttributeError
  或被 `_normalize` 静默重置默认还提示已保存）；
  ② 面板新增本机防护中间件——非 127.0.0.1/localhost 的 Host 一律 403（防 DNS rebinding），
  写方法带跨站 Origin 拦截（防 CSRF：/api/provider 可改写 key 去向=密钥外泄跳板）；
  ③ `provider_test` 空参数回退 `resolve()`——测试按钮与真实总结链路同源（原只读
  provider.json，legacy 用户测试误报未配 key）；
  ④ readiness 的 API key 来源按真实解析链显示（原 provider.json 场景误显示 api_key.txt，
  注册表场景显示空来源）；
  ⑤ 面板 Worker 不再注入 key 快照（原 `_worker_env_cache` 冻结首任务时的 key，设置页
  改 key 后子进程仍用旧值直至重启面板）；子进程统一经 provider_config 自解析；
  ⑥ tasklist/reg query 子进程补 `errors="replace"`——GBK 输出遇 PYTHONUTF8=1 在 reader
  线程解码崩溃 → readiness Clash 检测恒假绿"已退出"、注册表 key 回退静默失效（实测复现）；
  ⑦ `test_model` 对 HTTP 200 无 choices / 带 error 的响应判失败（原判成功=假阳性，
  部分 new-api 系中继配额不足时返回 200+error）；
  ⑧ provider.json 损坏时先改名 `.json.bad` 留档再落默认骨架（原静默覆盖，key 无法抢救）；
  load/save 加锁防并发丢更新；
  ⑨ 设置页删除与供应商卡片矛盾的旧 key 展示行（单一事实来源），徽章带 key 来源标注。
- **test**：verify.ps1 py_compile 白名单补 `provider_config.py`（此前该 untracked 模块是
  门禁盲区）；新增 `tests/test_provider_config.py`（双链解析/base_url 三种填法规整/
  normalize 边界/test_model 防假阳性/损坏留档，隔离真实配置无网络）。
- **docs**：README 架构图、runbook §5.5/§5.7/§5.8/§5.98/§10 供应商口径同步（OpenRouter
  ox-alpha → 双链描述）；backup_metadata.ps1 备份清单纳入 provider.json/api_key.txt；
  .gitignore 补 .claude/.codex（AionUi 本地脚手架符号链接）。

## 2026-08-25 · 终审修复（就绪检查/密钥回退/移除同步blrec/清理）

- **feat(dashboard) `ab523cf`**：仪表盘新增全流程就绪检查卡片（七项逐条✓/✗，60秒自动刷新+手动刷新按钮）；`/api/readiness` 端点；cookie 正则修正（值含转义引号时 `[^"]` 截断→MULTILINE 行匹配）；verify.ps1 面板未运行改自动拉起+FAIL 策略。
- **fix(critical) `fb4e561`**：面板移除房间同步通知 blrec 停止任务——原 remove 只删 settings.toml 不通知 blrec，blrec 内存任务继续录制直到容器重启（用户实锤：1937830735 移除后仍在录）；add 同理同步添加+启用 monitor/recorder，不再需重启容器。
- **fix(critical) `d1fb67d`**：api_key.txt BOM 导致全部总结失败——Out-File UTF8 写入的文件带 BOM，Python read_text(utf-8) 读出 \ufeff 前缀 → HTTP header 编码崩溃；get_api_key 用 utf-8-sig 剥 BOM + lstrip 双保险；9 段积压总结全部补齐。
- **fix(session) `36af6df`**：session.py get_api_key 补 api_key.txt 回退（与 summarize_host 统一）；14323359 场次聚合+场级总结实测通过。
- **chore `261ce7a`**：清理 10 个临时诊断脚本。

## 2026-08-25 · 片段库场次分组 + 忽略总结 + 密钥三级回退 + 乱码修复

- **feat(ux) `0d3f92e`**：片段库（原分段库）按场次分组渲染——组头含徽章/标题/时段/段数/
  忽略总结按钮/场级总结链接；场次筛选下拉 + `?session=ID` 深链；导航改名"分段库→片段库"；
  两页副标题自解释；status 后台常驻刷新线程（请求永不阻塞，二次请求 33ms）；
  cleanup 预览 GBK 乱码修复（Console UTF8）。
- **fix(key) `a347c5f`**：API key 三级回退（env → api_key.txt → 注册表）——解决
  "新进程/旧会话读不到环境变量"问题；summarize_host + panel/services 统一使用。
- **feat(session) `e8e5b29`/`9109e6e`**：场次聚合 v1 + 检查代理验收修复（详见上方条目）。
- **fix(presentation) `273f88c`**：REPORT 场次视图前置+内联内容；总结库场次行增强。
- **chore `261ce7a`**：清理 10 个临时诊断脚本。

## 2026-08-24 · 全链路重测通过 + selftest.ps1 固化

- **test `selftest.ps1`**：§5.98 SOP 固化为一键自测（合成3个静音视频→两阶段批处理→
  占位分支→场次聚类→REPORT→自动清理，不耗 API）。数据重置后的全链路重测 **PASS**：
  候选扫描/批量转写（模型单次加载）/占位srt分支/场次聚类(3段→1场已关闭)/报告 全部正确。
  首跑曾 FAIL——根因是测试文件名时分写成4位（非 blrec 命名规范），修正后通过；
  parse_start 对真实 blrec 输出的兼容性再次得到实证。

## 2026-08-24 · 通知/备份对抗评审加固

- **fix `notify.ps1` 加固**：节流键 GetHashCode→SHA256（跨会话稳定）；戳文件 24h 自动
  清理；info 级 SuppressPopup（仅落动作中心不抢注意力）、bad 级长驻留；非交互会话
  早期退出。status 磁盘告急 Text 改固定模板（动态数值曾绕过节流反复弹窗）。
- **fix `backup_metadata.ps1` 加固**：SYSTEM 会话 USERPROFILE 防护（兜底 C:\bilive_backup）；
  配置拷贝逐项容错（目标盘满不静默半失败）；注明 queue.json 原子写无撕裂风险。
- **chore**：清理无用文件 panel_err.log（0 字节）与 settings.toml.bak（CRCRLF 损坏态备份）。

## 2026-08-24 · P0 落地：通知渠道与元数据备份

- **feat `notify.ps1` + 接线**：Windows Toast（落动作中心）+ notify.log 双通道，30 分钟
  节流，Toast 失败降级仅日志；接线四类事件：status 劫持+停摆红牌、磁盘不足 1 天、
  process 批次失败、cleanup 执行与空间不足。失败路径已实测（坏视频→FAIL→Toast）。
- **feat `backup_metadata.ps1` + 计划任务 bilive-backup（每日 10:00）**：robocopy 增量
  备份 Videos 全部 srt/summary/场次 + settings/prompt/queue/keep → 用户目录
 （与 D 盘物理隔离）；**不含 /PURGE**——被清理分段的字幕/总结在备份端永久保留。
  实测：101 文件 2.4MB。README 路线图 P0 两项勾结。

## 2026-08-24 · 场次聚合功能上线

- **feat(session) `e8e5b29`**：场次聚合与整场总结 v1——`session.py` 把分段按 50 分钟
  间隔聚类为"场次"（派生视图幂等重算，双命名时间戳解析），场级二级总结
 （一句话/时间线/要点/高光/金句 + 段级附录，段指纹防过期），REPORT 场次视图，
  总结库场次徽章，CLI 合并/拆分/命名纠错。实测 TI 数据：08-22 场 21 段
 （37min 抖动正确合并）+ 08-23 场 25 段，真实 API 两场 50s 出稿。
  三视角子代理研讨（产品/架构/边界）定案：sessions=派生视图非持久事实。

## 2026-08-23 深夜 ~ 08-24 · 全量审查、测试、UI 与文档

- **fix `273f88c`**：场次交付物呈现达标（用户复核反馈）——REPORT 场次视图前置为主视图
  并内联实际内容（一句话/要点/高光，原仅状态表且沉底）；总结库场次行显示
  标题/时段/段数/一句话（原为假 mp4 名）且场次排前排；README 新增分段生命周期表。
- **fix `9109e6e`**：检查代理验收缺陷修复——merge_archived 无条件回填（修归档段二次
  重算元数据蒸发，附单测）；merge 后重算 end_est；segment_count 口径统一；split 参数校验。
- **docs `cfa84ca`/`75d181b`**：路线图新增场次聚合项（后已实现勾项）；场次功能文档三件套同步。
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
