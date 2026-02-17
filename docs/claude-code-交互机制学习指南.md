# Claude Code 交互机制学习指南

## 概述

这份指南帮助你理解 **Claude Code 的核心运行机制**，特别是其独特的 **"不等他结束，完全在过程中互动"** 的设计哲学。

## 核心机制：从串行到并行

### 问题：传统串行模式的局限

在 v6 及之前的版本，Agent 是这样工作的：

```
主 Agent: 启动任务 A
           |
           v
        [等待...]
           |
           v
        收到结果 A
           |
           v
        启动任务 B
           |
           v
        [等待...]
           |
           v
        收到结果 B
```

这种模式的问题显而易见：**主 Agent 在等待期间什么都做不了**。想象一个场景：

- 任务 A：分析代码质量（需要 2 分钟）
- 任务 B：运行测试套件（需要 3 分钟）
- 任务 C：检查安全漏洞（需要 1 分钟）

**串行执行**：2 + 3 + 1 = **6 分钟**
**并行执行**：max(2, 3, 1) = **3 分钟**

### 解决方案：v7 的后台执行机制

v7 引入了两个核心概念：

1. **后台执行 (Background Execution)**：任务在独立线程中运行，不阻塞主 Agent
2. **通知总线 (Notification Bus)**：任务完成时主动推送通知，而非被动轮询

```
主 Agent: 启动任务 A (后台) → 立即返回 task_id_A
           |
           v
        启动任务 B (后台) → 立即返回 task_id_B
           |
           v
        启动任务 C (后台) → 立即返回 task_id_C
           |
           v
        继续其他工作...
           |
           v
        ← [通知] 任务 C 完成了！
           |
           v
        ← [通知] 任务 A 完成了！
           |
           v
        ← [通知] 任务 B 完成了！
```

## 技术实现

### 1. BackgroundManager：后台任务管理器

```python
class BackgroundManager:
    def __init__(self):
        self._tasks: dict[str, BackgroundTask] = {}
        self._notifications: Queue = Queue()  # 线程安全的通知队列
        self._lock = threading.Lock()

    def run_in_background(self, func, task_type: str = "a") -> str:
        """在后台线程中运行函数，立即返回 task_id"""
        task_id = self._gen_id(task_type)
        bg_task = BackgroundTask(task_id=task_id, task_type=task_type)

        def wrapper():
            try:
                result = func()
                bg_task.output = result
                bg_task.status = "completed"
            except Exception as e:
                bg_task.output = f"Error: {e}"
                bg_task.status = "error"
            finally:
                bg_task.event.set()
                # 完成后推送通知
                self._notifications.put({
                    "type": "attachment",
                    "attachment": {
                        "type": "task_status",
                        "task_id": task_id,
                        "status": bg_task.status,
                        "summary": bg_task.output[:500],
                    },
                })

        thread = threading.Thread(target=wrapper, daemon=True)
        bg_task.thread = thread
        self._tasks[task_id] = bg_task
        thread.start()
        return task_id
```

**关键设计点**：

- **守护线程 (daemon=True)**：主进程退出时，后台线程自动终止
- **事件机制 (Event)**：提供等待/唤醒语义，支持阻塞式和非阻塞式查询
- **异常隔离**：后台任务的异常不会影响主 Agent
- **自动通知**：无论成功还是失败，完成后都会推送通知

### 2. 任务 ID 前缀约定

通过 ID 前缀，可以一眼识别任务类型：

| 前缀 | 类型 | 示例 | 用途 |
|------|------|------|------|
| `b` | bash 命令 | `b3f7c2` | 运行测试、lint、构建 |
| `a` | 子代理 | `a1c4e9` | 探索代码、分析文件 |
| `t` | Teammate | `t8d2a1` | v8+ 的持久协作者 |

### 3. 两个新工具

#### TaskOutput：获取后台任务结果

```python
# 阻塞式：等待任务完成
TaskOutput(task_id="a3f7c2", block=True, timeout=30000)
# 返回: {"status": "completed", "output": "...完整结果..."}

# 非阻塞式：立即返回当前状态
TaskOutput(task_id="a3f7c2", block=False)
# 返回: {"status": "running", "output": "...当前输出..."}
```

#### TaskStop：终止后台任务

```python
TaskStop(task_id="a3f7c2")
# 返回: {"task_id": "a3f7c2", "status": "stopped"}
```

### 4. 通知总线：推送而非轮询

通知总线是 v7 最精妙的设计。它采用 **推送模式** 而非轮询模式：

```python
def drain_notifications(self) -> list:
    """排空所有待处理的通知"""
    notifications = []
    while not self._notifications.empty():
        try:
            notifications.append(self._notifications.get_nowait())
        except Exception:
            break
    return notifications
```

**在 Agent 循环中的使用**：

```python
def agent_loop(messages: list) -> list:
    while True:
        # 每轮开始前，排空通知队列
        notifications = BG.drain_notifications()
        
        # 将通知注入到最后一条 user message 中
        if notifications:
            # 以 attachment 格式追加通知
            if messages[-1]["role"] == "user":
                content = messages[-1]["content"]
                if isinstance(content, list):
                    content.extend(notifications)
                else:
                    messages[-1]["content"] = [
                        {"type": "text", "text": content}
                    ] + notifications
            else:
                messages.append({"role": "user", "content": notifications})

        # 调用 API
        response = client.messages.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            ...
        )
```

**为什么是推送而非轮询？**

```
轮询模式（低效）：
  主 Agent: "任务完成了吗?" → API 调用 → 没有
  主 Agent: "任务完成了吗?" → API 调用 → 没有
  主 Agent: "任务完成了吗?" → API 调用 → 完成了！
  (浪费了 2 次 API 调用)

推送模式（高效）：
  主 Agent: [继续其他工作]
  系统: [完成时推送通知到队列]
  主 Agent: [下一轮自动看到通知]
  (零额外成本)
```

## 实际应用场景

### 场景 1：并行代码分析

```python
# 用户请求：分析整个项目的代码质量

# Agent 的做法（v7 并行模式）：
1. Task(background=True, prompt="分析 src/ 目录")     → task_id="a1c4e9"
2. Task(background=True, prompt="分析 tests/ 目录")   → task_id="a7b2d3"
3. Bash(background=True, command="eslint src/")       → task_id="b5e8f1"

# 三个任务并行运行，主 Agent 继续工作

4. [收到通知] task_id="b5e8f1" completed
5. [收到通知] task_id="a1c4e9" completed
6. TaskOutput("a7b2d3", block=True)  # 等待最后一个任务
7. 综合三个结果，生成报告
```

### 场景 2：长时间构建 + 继续编码

```python
# 用户请求：构建项目并修复警告

# Agent 的做法：
1. Bash(background=True, command="npm run build")  → task_id="b3a9f1"
2. [立即] 继续分析代码，识别潜在警告
3. [立即] 修复部分简单警告
4. [收到通知] 构建完成，查看构建输出
5. [基于构建结果] 修复剩余问题
```

### 场景 3：测试驱动开发

```python
# 用户请求：添加新功能并确保测试通过

# Agent 的做法：
1. 编写新功能代码
2. Bash(background=True, command="npm test")  → task_id="b7c2d1"
3. [不等测试完成] 开始编写文档
4. [收到通知] 测试失败
5. 查看失败详情，修复代码
6. Bash(background=True, command="npm test")  → task_id="b8e3f2"
7. [继续] 完善文档
8. [收到通知] 测试通过 ✓
```

## 交互模式对比

### 传统串行模式（v0-v6）

```
时间轴：
0s  ────┬──── 启动任务 A
        │
30s ────┼──── 任务 A 完成，主 Agent 被唤醒
        ├──── 启动任务 B
        │
60s ────┼──── 任务 B 完成，主 Agent 被唤醒
        ├──── 启动任务 C
        │
90s ────┴──── 任务 C 完成

总耗时：90 秒
主 Agent 空闲时间：60 秒（66%）
```

### 并行非阻塞模式（v7+）

```
时间轴：
0s  ────┬──── 启动任务 A (后台)
        ├──── 启动任务 B (后台)
        ├──── 启动任务 C (后台)
        ├──── 继续其他工作...
        │
30s ────┼──── [通知] 任务 A 完成
        │     [通知] 任务 C 完成
        ├──── 处理结果 A 和 C
        │
60s ────┼──── [通知] 任务 B 完成
        ├──── 处理结果 B
        │
65s ────┴──── 完成所有工作

总耗时：65 秒（节省 28%）
主 Agent 空闲时间：0 秒（0%）
```

## 与传统编程模式的对比

这种设计理念与软件工程中的异步编程完全一致：

| 传统编程 | Agent 架构 |
|---------|-----------|
| 同步函数调用 | v0-v6 串行模式 |
| 异步/Promise | v7 后台执行 |
| 回调函数 | 通知总线 |
| Event Loop | Agent Loop + 通知排空 |

**相似的演进路径**：

- **Node.js**：从回调地狱到 Promise/async-await
- **Go**：goroutine + channel
- **Python**：asyncio + await
- **Claude Code**：后台执行 + 通知总线

核心思想都是：**不要等结果，发起请求后继续工作，结果来了再处理**。

## 为 v8 Teammate 奠定基础

注意到 BackgroundManager 的 ID 前缀中，`t` 已经预留给 Teammate（队友）。v8 引入的 Teammate 本质上是一种"永不结束的后台任务"：

```python
# v8 中，Teammate 也通过后台线程运行
teammate_id = BG.run_in_background(
    lambda: teammate_loop(...),
    task_type="teammate"
)
# 返回 "t8d2a1" - 't' 前缀表示这是一个 teammate
```

Teammate 通过同样的通知总线与主 Agent 通信。v7 的基础设施是 v8 多 Agent 协作的底层依赖。

## 实现细节：线程安全

```python
@dataclass
class BackgroundTask:
    task_id: str                    # 唯一标识
    task_type: str                  # "bash" 或 "agent"
    thread: threading.Thread        # 执行线程
    output: str = ""                # 执行结果
    status: str = "running"         # running | completed | error | stopped
    event: threading.Event          # 完成信号

class BackgroundManager:
    def __init__(self):
        self._tasks: dict[str, BackgroundTask] = {}
        self._notifications: Queue = Queue()  # Queue 是线程安全的
        self._lock = threading.Lock()         # 保护 _tasks 字典
```

**关键安全保证**：

1. **Queue 是线程安全的**：多个线程可以同时 put/get
2. **Lock 保护共享状态**：访问 `_tasks` 字典时加锁
3. **Event 提供同步原语**：`event.wait()` 支持阻塞式等待
4. **守护线程**：主进程退出时自动清理

## 性能考量

### 输出文件系统

后台任务的输出保存到磁盘 `.task_outputs/{task_id}.output`：

```python
OUTPUT_DIR = WORKDIR / ".task_outputs"

def _write_output(self, task_id, content):
    max_output_chars = int(os.getenv("TASK_MAX_OUTPUT_LENGTH", "32000"))
    path = OUTPUT_DIR / f"{task_id}.output"
    truncated = content[:max_output_chars]
    with open(path, "a") as f:
        f.write(truncated)
    return path
```

**为什么需要持久化？**

1. **防止内存膨胀**：大型输出（如完整测试日志）不会占用内存
2. **上下文压缩友好**：即使对话上下文被压缩，输出仍然可访问
3. **通知摘要**：通知只包含 500 字符的摘要，完整输出在文件中

### 通知摘要策略

```python
self._notifications.put({
    "type": "attachment",
    "attachment": {
        "type": "task_status",
        "task_id": task_id,
        "task_type": bg_task.task_type,
        "status": bg_task.status,
        "summary": bg_task.output[:500],  # 只推送前 500 字符
        "output_file": str(output_path),  # 完整输出的文件路径
    },
})
```

这种设计确保：
- 通知轻量，不会膨胀消息历史
- Agent 可以决定是否需要完整输出
- 完整输出可通过 TaskOutput 工具获取

## 总结

v7 的后台执行机制代表了 Agent 架构中的一个根本性范式转变：

| 维度 | v0-v6 串行模式 | v7 并行模式 |
|------|---------------|-----------|
| 执行方式 | 阻塞等待 | 后台执行 |
| 通信方式 | 同步返回 | 异步通知 |
| 时间效率 | 线性累加 | 并行重叠 |
| Agent 利用率 | 等待期间空闲 | 持续工作 |
| 适用场景 | 简单顺序任务 | 复杂并行任务 |

**核心哲学**：

> **不要等结果，发起请求后继续工作。结果来了，系统会主动告诉你。**

这就是 Claude Code 的 **"不等他结束，完全在过程中互动"** 机制的本质。

## 延伸阅读

- [v7: 后台任务与通知 Bus](./v7-后台任务与通知Bus.md) - 技术文档
- [v7文章](../articles/v7文章.md) - 公众号风格文章
- [v8: 团队通信](./v8-团队通信.md) - 了解如何在 v7 基础上构建多 Agent 系统

## 实践建议

1. **先理解 v0**：从最简单的 Bash Agent 开始，理解核心循环
2. **对比 v6 和 v7**：运行两个版本，感受串行和并行的区别
3. **阅读 v7 源码**：重点关注 `BackgroundManager` 和通知注入逻辑
4. **尝试修改**：添加新的后台任务类型，理解扩展机制
5. **构建项目**：使用 v7 模式构建你自己的 Agent

---

**从串行到并行，从等待到通知。这是 Agent 效率革命的关键一步。**

[@baicai003](https://x.com/baicai003) | [shareAI Lab](https://github.com/shareAI-lab)
