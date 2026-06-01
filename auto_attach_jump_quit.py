import gdb
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

gdb_set_silent("handle SIGINT noprint nostop pass")
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

            eval_and_print(self.print_expr)

            sal_tuple = gdb.decode_line(self.jump_loc)[1]
            if not sal_tuple:
                post_detach_and_quit(f"[-] cannot decode jump location: {self.jump_loc}")
                return True

            target = sal_tuple[0].pc

            print(f"[+] set current thread $pc = {self.jump_loc} / 0x{target:x}", flush=True)

            # 只修改命中断点线程
            gdb.execute(f"thread {hit_thread.num}", to_string=True)
            gdb.execute(f"set $pc = 0x{target:x}", to_string=True)

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
