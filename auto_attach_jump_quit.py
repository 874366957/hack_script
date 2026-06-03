import gdb
import os
import sys
import traceback


def gdb_set_silent(cmd):
    try:
        gdb.execute(cmd, to_string=True)
    except Exception:
        pass


gdb_set_silent("set pagination off")
gdb_set_silent("set confirm off")
gdb_set_silent("set target-async on")
gdb_set_silent("set print thread-events off")
gdb_set_silent("set print inferior-events off")
gdb_set_silent("set verbose off")

# Timeout cleanup uses GDB's interrupt command. Keep the resulting SIGINT
# inside GDB so it cannot be delivered to gaussdb when detaching.
gdb_set_silent("handle SIGINT noprint stop nopass")
gdb_set_silent("handle SIGUSR1 noprint nostop pass")
gdb_set_silent("handle SIGUSR2 noprint nostop pass")
gdb_set_silent("handle SIG36 noprint nostop pass")
gdb_set_silent("handle SIGSTOP noprint nostop pass")

cleanup_pending = False
cleaning = False


def detach_and_quit(reason=""):
    global cleaning, cleanup_pending

    if cleaning:
        return

    cleaning = True
    cleanup_pending = False

    if reason:
        print(reason, flush=True)

    try:
        print("[+] detach inferior", flush=True)
        gdb.execute("detach", to_string=True)
    except Exception as e:
        print(f"[!] detach failed: {e}", flush=True)

    try:
        print("[+] quit gdb", flush=True)
        gdb.execute("quit", to_string=True)
    except Exception:
        pass


def post_detach_and_quit(reason=""):
    gdb.post_event(lambda: detach_and_quit(reason))


def request_interrupt_then_detach(reason=""):
    """
    不给 gdb 进程发 SIGINT。
    只在 GDB 内部执行 interrupt 命令。
    等 stop event 到来后再 detach。
    """
    global cleanup_pending

    if reason:
        print(reason, flush=True)

    cleanup_pending = True

    try:
        gdb.execute("interrupt", to_string=True)
    except Exception as e:
        print(f"[!] gdb interrupt command failed: {e}", flush=True)
        gdb.post_event(lambda: detach_and_quit("[!] fallback detach and quit"))


def on_stop(event):
    global cleanup_pending

    if cleanup_pending:
        if isinstance(event, gdb.SignalEvent) and event.stop_signal == "SIGINT":
            # Explicitly discard the synthetic timeout interrupt before detach.
            gdb_set_silent("queue-signal 0")
        gdb.post_event(lambda: detach_and_quit("[+] stopped after interrupt, detach and quit gdb"))


gdb.events.stop.connect(on_stop)


class RequestDetach(gdb.Command):
    """
    Bash 超时时向 GDB stdin 写入：
        request_detach
    """

    def __init__(self):
        super().__init__("request_detach", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        request_interrupt_then_detach("[!] timeout requested by bash")


RequestDetach()


def eval_and_print(expr):
    if not expr:
        return

    try:
        val = gdb.parse_and_eval(expr)
        print(f"__GDB_PRINT_RESULT__={val}", flush=True)
    except Exception as e:
        print(f"__GDB_PRINT_ERROR__={e}", flush=True)


def allow_cross_function_jump():
    return os.environ.get("ALLOW_CROSS_FUNCTION_JUMP") == "1"


def block_function_name(block):
    while block:
        try:
            function = block.function
        except Exception:
            function = None

        if function:
            return function.print_name or function.name

        try:
            block = block.superblock
        except Exception:
            return None

    return None


def function_name_for_pc(pc):
    try:
        return block_function_name(gdb.block_for_pc(pc))
    except Exception:
        return None


def selected_pc():
    try:
        return int(gdb.parse_and_eval("$pc"))
    except Exception:
        return None


def info_symbol_for_pc(pc):
    try:
        return gdb.execute(f"info symbol 0x{pc:x}", to_string=True).strip()
    except Exception as e:
        return f"<info symbol failed: {e}>"


def looks_like_static_initializer(text):
    if not text:
        return False

    static_init_markers = [
        "_GLOBAL__sub_I_",
        "__static_initialization_and_destruction_0",
        "__libc_csu_init",
        "InitBeforeMain",
    ]
    return any(marker in text for marker in static_init_markers)


def sal_location(sal):
    symtab = getattr(sal, "symtab", None)
    line = getattr(sal, "line", None)

    if symtab:
        try:
            filename = symtab.fullname()
        except Exception:
            filename = symtab.filename
    else:
        filename = "<unknown>"

    if line:
        return f"{filename}:{line}"

    return filename


def describe_candidate(index, sal):
    try:
        pc = int(sal.pc)
    except Exception:
        pc = None

    if pc is None:
        print(f"[+] jump candidate[{index}]: pc=<none> loc={sal_location(sal)}", flush=True)
        return None

    function = function_name_for_pc(pc)
    symbol = info_symbol_for_pc(pc)
    print(
        f"[+] jump candidate[{index}]: pc=0x{pc:x} loc={sal_location(sal)} "
        f"function={function or '<unknown>'} symbol={symbol}",
        flush=True,
    )

    return {
        "pc": pc,
        "function": function,
        "location": sal_location(sal),
        "symbol": symbol,
    }


def resolve_jump_target(jump_loc, current_function):
    sal_tuple = gdb.decode_line(jump_loc)[1]
    if not sal_tuple:
        print(f"[-] cannot decode jump location: {jump_loc}", flush=True)
        return None

    candidates = []
    print(f"[+] decoded {len(sal_tuple)} jump candidate(s) for {jump_loc}", flush=True)

    for index, sal in enumerate(sal_tuple):
        candidate = describe_candidate(index, sal)
        if candidate:
            candidates.append(candidate)

    if not candidates:
        print(f"[-] no usable jump target pc for {jump_loc}", flush=True)
        return None

    same_function_candidates = [
        candidate
        for candidate in candidates
        if current_function and candidate["function"] == current_function
    ]

    if same_function_candidates:
        candidate = same_function_candidates[0]
        if (
            not allow_cross_function_jump()
            and (
                looks_like_static_initializer(candidate["function"])
                or looks_like_static_initializer(candidate["symbol"])
            )
        ):
            print(
                f"[-] refuse jump into static initializer target: "
                f"function={candidate['function'] or '<unknown>'} symbol={candidate['symbol']}",
                flush=True,
            )
            return None
        return candidate

    if allow_cross_function_jump():
        candidate = candidates[0]
        print(
            f"[!] ALLOW_CROSS_FUNCTION_JUMP=1: using cross-function target "
            f"{candidate['function'] or '<unknown>'} at 0x{candidate['pc']:x}",
            flush=True,
        )
        return candidate

    print(
        f"[-] refuse cross-function jump: current function={current_function or '<unknown>'}; "
        f"jump_loc={jump_loc}. Set ALLOW_CROSS_FUNCTION_JUMP=1 to override.",
        flush=True,
    )
    return None


def global_exception_handler(exc_type, exc_value, tb):
    print("[!] Unhandled Python exception in GDB script:", flush=True)
    traceback.print_exception(exc_type, exc_value, tb)
    post_detach_and_quit("[!] cleanup after unhandled exception")


sys.excepthook = global_exception_handler


class AutoJumpQuitBP(gdb.Breakpoint):
    def __init__(self, break_loc, jump_loc, print_expr=None):
        super().__init__(break_loc)
        self.silent = True
        self.jump_loc = jump_loc
        self.print_expr = print_expr

    def stop(self):
        try:
            print(f"[+] breakpoint hit: {self.location}", flush=True)

            hit_thread = gdb.selected_thread()
            print(f"[+] hit thread: num={hit_thread.num}, ptid={hit_thread.ptid}", flush=True)

            gdb.execute(f"thread {hit_thread.num}", to_string=True)
            current_pc = selected_pc()
            current_function = function_name_for_pc(current_pc) if current_pc is not None else None
            current_pc_text = f"0x{current_pc:x}" if current_pc is not None else "<unknown>"
            print(
                f"[+] current pc={current_pc_text} function={current_function or '<unknown>'}",
                flush=True,
            )

            eval_and_print(self.print_expr)

            target = resolve_jump_target(self.jump_loc, current_function)
            if not target:
                post_detach_and_quit("[-] unsafe jump target, detach and quit gdb")
                return True

            target_pc = target["pc"]
            target_function = target["function"]
            if current_function != target_function:
                print(
                    f"[!] cross-function jump: {current_function or '<unknown>'} -> "
                    f"{target_function or '<unknown>'}",
                    flush=True,
                )

            print(f"[+] set current thread $pc = {self.jump_loc} / 0x{target_pc:x}", flush=True)

            # 只修改命中断点线程
            gdb.execute(f"thread {hit_thread.num}", to_string=True)
            gdb.execute(f"set $pc = 0x{target_pc:x}", to_string=True)

            post_detach_and_quit("[+] jump done, detach and quit gdb")

        except BaseException as e:
            post_detach_and_quit(f"[-] breakpoint handler failed: {e}")

        return True


class AutoAttachJumpQuit(gdb.Command):
    """
    Usage:
        auto_attach_jump_quit PID break_loc jump_loc [print_expr]

    Example:
        auto_attach_jump_quit 12345 test.c:123 test.c:456 "gs_thread_self()"
    """

    def __init__(self):
        super().__init__("auto_attach_jump_quit", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        try:
            args = gdb.string_to_argv(arg)

            if len(args) < 3 or len(args) > 4:
                print("Usage: auto_attach_jump_quit PID break_loc jump_loc [print_expr]", flush=True)
                return

            pid = args[0]
            break_loc = args[1]
            jump_loc = args[2]
            print_expr = args[3] if len(args) >= 4 else None

            print(f"[+] attach {pid}", flush=True)
            gdb.execute(f"attach {pid}", to_string=True)

            AutoJumpQuitBP(break_loc, jump_loc, print_expr)

            print(f"[+] breakpoint set at {break_loc}", flush=True)
            print(f"[+] will jump to {jump_loc}", flush=True)

            if print_expr:
                print(f"[+] will print expr: {print_expr}", flush=True)

            print(f"__GDB_ATTACH_READY__={pid}", flush=True)

            # 必须使用异步 continue，才能让 bash 之后通过 stdin 写 request_detach
            gdb.execute("continue &", to_string=True)

        except BaseException as e:
            post_detach_and_quit(f"[-] command failed: {e}")


AutoAttachJumpQuit()
print("[+] auto_attach_jump_quit.py loaded", flush=True)
