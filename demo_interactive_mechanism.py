#!/usr/bin/env python3
"""
演示 Claude Code 的交互机制 - "不等他结束，完全在过程中互动"

这个演示脚本展示了 v7 后台执行机制的核心特性：
1. 后台并行执行多个任务
2. 主线程继续工作不被阻塞
3. 通过通知总线接收完成通知
4. 灵活的阻塞/非阻塞查询模式

使用方法：
    python demo_interactive_mechanism.py
"""

import time
import threading
from queue import Queue
from dataclasses import dataclass, field
import uuid


# =============================================================================
# 简化版 BackgroundManager（演示用）
# =============================================================================

@dataclass
class BackgroundTask:
    task_id: str
    task_name: str
    thread: threading.Thread = field(repr=False, default=None)
    output: str = ""
    status: str = "running"
    event: threading.Event = field(default_factory=threading.Event, repr=False)


class BackgroundManager:
    """后台任务管理器 - 演示版"""
    
    def __init__(self):
        self._tasks: dict[str, BackgroundTask] = {}
        self._notifications: Queue = Queue()
        self._lock = threading.Lock()
    
    def run_in_background(self, func, task_name: str, task_type: str = "a") -> str:
        """在后台线程中运行函数，立即返回 task_id"""
        prefix = {"bash": "b", "agent": "a"}.get(task_type, "a")
        task_id = f"{prefix}{uuid.uuid4().hex[:6]}"
        
        bg_task = BackgroundTask(task_id=task_id, task_name=task_name)
        
        def wrapper():
            try:
                print(f"  [{task_id}] {task_name} 开始执行...")
                result = func()
                bg_task.output = result
                bg_task.status = "completed"
                print(f"  [{task_id}] {task_name} 完成！")
            except Exception as e:
                bg_task.output = f"Error: {e}"
                bg_task.status = "error"
                print(f"  [{task_id}] {task_name} 失败: {e}")
            finally:
                bg_task.event.set()
                # 推送完成通知
                self._notifications.put({
                    "task_id": task_id,
                    "task_name": task_name,
                    "status": bg_task.status,
                    "summary": bg_task.output[:100],
                })
        
        thread = threading.Thread(target=wrapper, daemon=True)
        bg_task.thread = thread
        
        with self._lock:
            self._tasks[task_id] = bg_task
        
        thread.start()
        return task_id
    
    def get_output(self, task_id: str, block: bool = True, timeout: int = 30) -> dict:
        """获取后台任务结果"""
        with self._lock:
            bg_task = self._tasks.get(task_id)
        
        if not bg_task:
            return {"error": f"Task {task_id} not found"}
        
        if block and bg_task.status == "running":
            print(f"  [主 Agent] 等待 {task_id} 完成...")
            bg_task.event.wait(timeout=timeout)
        
        return {
            "task_id": task_id,
            "status": bg_task.status,
            "output": bg_task.output,
        }
    
    def drain_notifications(self) -> list:
        """排空所有待处理的通知"""
        notifications = []
        while not self._notifications.empty():
            try:
                notifications.append(self._notifications.get_nowait())
            except Exception:
                break
        return notifications


# =============================================================================
# 模拟任务函数
# =============================================================================

def analyze_code_quality(seconds: int) -> str:
    """模拟代码质量分析任务"""
    time.sleep(seconds)
    return f"代码质量分析完成：发现 5 个警告，2 个错误（耗时 {seconds}s）"


def run_tests(seconds: int) -> str:
    """模拟测试运行任务"""
    time.sleep(seconds)
    return f"测试运行完成：247 个测试通过，3 个失败（耗时 {seconds}s）"


def check_security(seconds: int) -> str:
    """模拟安全检查任务"""
    time.sleep(seconds)
    return f"安全检查完成：未发现高危漏洞（耗时 {seconds}s）"


def build_project(seconds: int) -> str:
    """模拟项目构建任务"""
    time.sleep(seconds)
    return f"项目构建完成：生成 bundle.js (1.2MB)（耗时 {seconds}s）"


# =============================================================================
# 演示场景
# =============================================================================

def demo_serial_mode():
    """演示 1：传统串行模式（v0-v6）"""
    print("\n" + "="*80)
    print("演示 1: 传统串行模式（v0-v6）- 一个任务一个任务地做")
    print("="*80)
    
    start_time = time.time()
    
    print("\n[主 Agent] 启动任务 A: 代码质量分析")
    result_a = analyze_code_quality(2)
    print(f"[主 Agent] 收到结果: {result_a}")
    
    print("\n[主 Agent] 启动任务 B: 运行测试")
    result_b = run_tests(3)
    print(f"[主 Agent] 收到结果: {result_b}")
    
    print("\n[主 Agent] 启动任务 C: 安全检查")
    result_c = check_security(1)
    print(f"[主 Agent] 收到结果: {result_c}")
    
    elapsed = time.time() - start_time
    print(f"\n总耗时: {elapsed:.1f} 秒")
    print("分析: 主 Agent 在等待期间完全阻塞，无法做其他工作")


def demo_parallel_mode():
    """演示 2：并行非阻塞模式（v7）"""
    print("\n" + "="*80)
    print("演示 2: 并行非阻塞模式（v7）- 同时启动多个任务")
    print("="*80)
    
    bg = BackgroundManager()
    start_time = time.time()
    
    # 同时启动三个后台任务
    print("\n[主 Agent] 启动任务 A (后台): 代码质量分析")
    task_a = bg.run_in_background(
        lambda: analyze_code_quality(2),
        task_name="代码质量分析",
        task_type="agent"
    )
    print(f"[主 Agent] 立即返回 task_id={task_a}，继续工作...")
    
    print("\n[主 Agent] 启动任务 B (后台): 运行测试")
    task_b = bg.run_in_background(
        lambda: run_tests(3),
        task_name="运行测试",
        task_type="bash"
    )
    print(f"[主 Agent] 立即返回 task_id={task_b}，继续工作...")
    
    print("\n[主 Agent] 启动任务 C (后台): 安全检查")
    task_c = bg.run_in_background(
        lambda: check_security(1),
        task_name="安全检查",
        task_type="agent"
    )
    print(f"[主 Agent] 立即返回 task_id={task_c}，继续工作...")
    
    print("\n[主 Agent] 三个任务都已启动，我可以继续做其他事情...")
    print("[主 Agent] 例如：分析依赖关系、生成文档等")
    time.sleep(0.5)
    
    # 演示通知总线
    print("\n[主 Agent] 检查通知总线...")
    time.sleep(1)  # 等待一些任务完成
    
    notifications = bg.drain_notifications()
    if notifications:
        print(f"[主 Agent] 收到 {len(notifications)} 个通知:")
        for notif in notifications:
            print(f"  - {notif['task_name']} ({notif['task_id']}): {notif['status']}")
            print(f"    摘要: {notif['summary']}")
    
    # 获取剩余任务的结果
    print("\n[主 Agent] 获取所有任务的完整结果...")
    result_a = bg.get_output(task_a, block=True)
    result_b = bg.get_output(task_b, block=True)
    result_c = bg.get_output(task_c, block=True)
    
    print(f"\n任务 A 结果: {result_a['output']}")
    print(f"任务 B 结果: {result_b['output']}")
    print(f"任务 C 结果: {result_c['output']}")
    
    elapsed = time.time() - start_time
    print(f"\n总耗时: {elapsed:.1f} 秒")
    print("分析: 三个任务并行执行，总时间等于最长任务的时间")


def demo_mixed_mode():
    """演示 3：混合模式 - 部分并行，部分等待"""
    print("\n" + "="*80)
    print("演示 3: 混合模式 - 灵活的阻塞/非阻塞控制")
    print("="*80)
    
    bg = BackgroundManager()
    start_time = time.time()
    
    print("\n场景: 构建项目，同时分析代码，构建完成后运行测试")
    
    # 启动构建任务（后台）
    print("\n[主 Agent] 启动构建任务（后台）...")
    task_build = bg.run_in_background(
        lambda: build_project(3),
        task_name="项目构建",
        task_type="bash"
    )
    
    # 同时启动代码分析（后台）
    print("[主 Agent] 启动代码分析（后台）...")
    task_analyze = bg.run_in_background(
        lambda: analyze_code_quality(2),
        task_name="代码分析",
        task_type="agent"
    )
    
    print("\n[主 Agent] 两个任务已启动，我继续做其他工作...")
    time.sleep(1)
    
    # 非阻塞查询
    print("\n[主 Agent] 非阻塞查询任务状态...")
    status_build = bg.get_output(task_build, block=False)
    print(f"  构建任务: {status_build['status']}")
    status_analyze = bg.get_output(task_analyze, block=False)
    print(f"  分析任务: {status_analyze['status']}")
    
    # 等待构建完成（阻塞）
    print("\n[主 Agent] 现在需要构建结果，等待构建完成...")
    result_build = bg.get_output(task_build, block=True)
    print(f"  {result_build['output']}")
    
    # 构建完成后，启动测试
    print("\n[主 Agent] 构建完成，启动测试...")
    task_test = bg.run_in_background(
        lambda: run_tests(2),
        task_name="运行测试",
        task_type="bash"
    )
    
    # 获取分析结果（可能已经完成）
    result_analyze = bg.get_output(task_analyze, block=True)
    print(f"\n[主 Agent] 分析结果: {result_analyze['output']}")
    
    # 获取测试结果
    result_test = bg.get_output(task_test, block=True)
    print(f"[主 Agent] 测试结果: {result_test['output']}")
    
    elapsed = time.time() - start_time
    print(f"\n总耗时: {elapsed:.1f} 秒")
    print("分析: 灵活控制什么时候等待，什么时候继续工作")


def demo_notification_bus():
    """演示 4：通知总线的工作机制"""
    print("\n" + "="*80)
    print("演示 4: 通知总线 - 被动接收 vs 主动查询")
    print("="*80)
    
    bg = BackgroundManager()
    
    print("\n[主 Agent] 启动 3 个任务，不同完成时间...")
    task_a = bg.run_in_background(
        lambda: check_security(1),
        task_name="快速任务",
        task_type="agent"
    )
    task_b = bg.run_in_background(
        lambda: analyze_code_quality(2),
        task_name="中速任务",
        task_type="agent"
    )
    task_c = bg.run_in_background(
        lambda: run_tests(3),
        task_name="慢速任务",
        task_type="bash"
    )
    
    print("\n[主 Agent] 模拟 Agent 循环，每 0.8 秒检查一次通知...")
    for i in range(5):
        time.sleep(0.8)
        notifications = bg.drain_notifications()
        if notifications:
            print(f"\n第 {i+1} 轮: 收到 {len(notifications)} 个通知")
            for notif in notifications:
                print(f"  ✓ {notif['task_name']} 完成")
        else:
            print(f"\n第 {i+1} 轮: 暂无通知")
    
    print("\n分析: 通知是推送的，不需要主动轮询，零额外成本")


# =============================================================================
# 主函数
# =============================================================================

def main():
    print("\n╔════════════════════════════════════════════════════════════════════════╗")
    print("║  Claude Code 交互机制演示 - '不等他结束，完全在过程中互动'          ║")
    print("╚════════════════════════════════════════════════════════════════════════╝")
    
    # 运行所有演示
    demo_serial_mode()
    time.sleep(1)
    
    demo_parallel_mode()
    time.sleep(1)
    
    demo_mixed_mode()
    time.sleep(1)
    
    demo_notification_bus()
    
    # 总结
    print("\n" + "="*80)
    print("总结")
    print("="*80)
    print("""
核心机制对比:

1. 串行模式（v0-v6）:
   - 一次只能执行一个任务
   - 等待期间主 Agent 完全阻塞
   - 总时间 = 所有任务时间之和
   - 适用场景：简单顺序任务

2. 并行模式（v7+）:
   - 同时执行多个任务
   - 主 Agent 继续工作，不被阻塞
   - 总时间 = 最长任务的时间
   - 通知总线提供完成通知
   - 适用场景：复杂并行任务

3. 混合模式:
   - 灵活控制阻塞/非阻塞
   - 根据依赖关系决定等待时机
   - 兼顾效率和逻辑正确性

这就是 Claude Code 的 "不等他结束，完全在过程中互动" 机制的本质！
    """)


if __name__ == "__main__":
    main()
