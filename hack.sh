
#!/usr/bin/env bash
set -u

GAUSS_PID="$1"
BREAK_LOC="$2"
JUMP_LOC="$3"
CASE_TYPE="$4"
CASE_PAYLOAD="$5"
TIMEOUT_SEC="${6:-30}"

thread_id=""

coproc GDBPROC {
    stdbuf -oL -eL gdb -q -x auto_attach_jump_quit.py \
        -ex "auto_attach_jump_quit $GAUSS_PID $BREAK_LOC $JUMP_LOC 'gs_thread_self()'" \
        2>&1
}

gdb_pid="$GDBPROC_PID"
deadline=$((SECONDS + TIMEOUT_SEC))
case_started=0

while :; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "[bash] timeout, ask gdb to detach safely" >&2

        # 关键：不要 kill -INT gdb
        # 通过 GDB stdin 执行自定义命令
        printf "request_detach\n" >&"${GDBPROC[1]}" 2>/dev/null || true

        wait "$gdb_pid" 2>/dev/null || true
        echo "[bash] STATUS=TIMEOUT" >&2
        exit 124
    fi

    if IFS= read -r -t 1 line <&"${GDBPROC[0]}"; then
        case "$line" in
            __GDB_ATTACH_READY__=*)
                echo "[bash] gdb attach ready" >&2

                if [ "$case_started" -eq 0 ]; then
                    case_started=1
                    echo "[bash] run case" >&2
                    ./run_case.sh "$CASE_TYPE" "$CASE_PAYLOAD" >&2 &
                fi
                ;;

            __GDB_PRINT_RESULT__=*)
                thread_id="${line#__GDB_PRINT_RESULT__=}"
                echo "[bash] thread_id=$thread_id" >&2
                break
                ;;

            __GDB_PRINT_ERROR__=*)
                echo "[bash] $line" >&2
                ;;
        esac
    else
        if ! kill -0 "$gdb_pid" 2>/dev/null; then
            break
        fi
    fi
done

wait "$gdb_pid" 2>/dev/null || true

if [ -z "$thread_id" ]; then
    echo "[bash] ERROR: no thread_id captured" >&2
    exit 1
fi

echo "$thread_id"
exit 0
