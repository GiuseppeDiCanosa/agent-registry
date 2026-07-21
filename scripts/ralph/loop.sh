#!/usr/bin/env bash
# GENERATED FROM SPEC — DO NOT EDIT DIRECTLY
# Source: openspec/specs/spec-loop/spec.md
#
# Ralph-style loop for the apply phase of an active OpenSpec change.
# Each iteration re-invokes a headless agent as a FRESH process with a fixed
# prompt; state lives on disk (tasks.md, git), never in the conversation.
# The loop trusts only mechanical evidence: the tasks.md checklist and
# scripts/verify.sh. It never trusts the agent's own claim of completion.
#
# When verification goes red, the loop refuses to move on: it switches to a
# repair prompt ("make the code satisfy the spec") and returns to task mode
# only once scripts/verify.sh is green again.
#
# Usage (from the project root):
#   bash scripts/ralph/loop.sh [change-name]
#
# Environment:
#   AGENT_CMD                 headless agent command reading the prompt on
#                             stdin (default: "claude -p"). Set permission
#                             flags here explicitly if you accept their risk —
#                             this script never adds any.
#   MAX_ITERATIONS            hard iteration cap (default: 25)
#   MAX_CONSECUTIVE_FAILURES  abort after N no-progress task iterations in
#                             a row (default: 1)
#   MAX_FIX_ATTEMPTS          abort after N consecutive repair iterations
#                             that leave verification red (default: 10)
#
# Stop conditions (priority order): STOP file in this script's directory,
# MAX_ITERATIONS, MAX_CONSECUTIVE_FAILURES, MAX_FIX_ATTEMPTS, success (zero
# unchecked tasks AND scripts/verify.sh green).
set -uo pipefail

AGENT_CMD="${AGENT_CMD:-claude -p}"
MAX_ITERATIONS="${MAX_ITERATIONS:-25}"
MAX_CONSECUTIVE_FAILURES="${MAX_CONSECUTIVE_FAILURES:-1}"
MAX_FIX_ATTEMPTS="${MAX_FIX_ATTEMPTS:-10}"

RUNNER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_FILE="$RUNNER_DIR/LOOP_PROMPT.md"
FIX_PROMPT_FILE="$RUNNER_DIR/LOOP_FIX_PROMPT.md"
STOP_FILE="$RUNNER_DIR/STOP"
LOG_DIR="$RUNNER_DIR/logs"

fatal() { echo "loop: $*" >&2; exit 1; }

[ -f "$PROMPT_FILE" ] || fatal "missing prompt file: $PROMPT_FILE"
[ -f "$FIX_PROMPT_FILE" ] || fatal "missing repair prompt file: $FIX_PROMPT_FILE"
[ -x "scripts/verify.sh" ] || fatal "scripts/verify.sh not found or not executable — run this from the project root of a spec-as-source project"
[ -d "openspec/changes" ] || fatal "no openspec/changes/ directory — is this an OpenSpec project?"

# --- resolve the active change -------------------------------------------
CHANGE_NAME="${1:-}"
if [ -z "$CHANGE_NAME" ]; then
    candidates=()
    for d in openspec/changes/*/; do
        name="$(basename "$d")"
        [ "$name" = "archive" ] && continue
        [ -f "${d}tasks.md" ] && candidates+=("$name")
    done
    [ "${#candidates[@]}" -eq 0 ] && fatal "no active change with a tasks.md found"
    [ "${#candidates[@]}" -gt 1 ] && fatal "multiple active changes (${candidates[*]}) — pass one explicitly"
    CHANGE_NAME="${candidates[0]}"
fi

TASKS_FILE="openspec/changes/$CHANGE_NAME/tasks.md"
[ -f "$TASKS_FILE" ] || fatal "no tasks file at $TASKS_FILE"

count_unchecked() { grep -cE '^[[:space:]]*- \[ \]' "$TASKS_FILE" || true; }

run_verify() { bash scripts/verify.sh >"$last_verify_log" 2>&1; }

build_prompt() {
    # Emits the prompt for the current mode on stdout.
    if [ "$mode" = "fix" ]; then
        sed "s/{{CHANGE_NAME}}/$CHANGE_NAME/g" "$FIX_PROMPT_FILE"
        echo ""
        echo "## Latest verification output (tail)"
        echo '```'
        tail -n 60 "$last_verify_log" 2>/dev/null || echo "(no verification log yet)"
        echo '```'
    else
        sed "s/{{CHANGE_NAME}}/$CHANGE_NAME/g" "$PROMPT_FILE"
    fi
}

mkdir -p "$LOG_DIR"
[ -f "$STOP_FILE" ] && fatal "STOP file present at $STOP_FILE — remove it to start"

echo "loop: change=$CHANGE_NAME agent=[$AGENT_CMD] max_iterations=$MAX_ITERATIONS"
echo "loop: to abort at any time: touch $STOP_FILE"

iteration=0
consecutive_failures=0
fix_attempts=0
mode="task"
last_verify_log="$LOG_DIR/iter-0-verify.log"

# Already converged? Don't spend a single iteration.
if [ "$(count_unchecked)" -eq 0 ] && run_verify; then
    echo "loop: nothing to do — checklist already complete and verification green."
    exit 0
fi

while true; do
    # -- stop conditions checked before spending an iteration --------------
    if [ -f "$STOP_FILE" ]; then
        echo "loop: STOP file detected — aborting before iteration $((iteration + 1))." >&2
        exit 2
    fi
    if [ "$iteration" -ge "$MAX_ITERATIONS" ]; then
        echo "loop: iteration cap reached ($MAX_ITERATIONS) without convergence — aborting." >&2
        exit 3
    fi

    iteration=$((iteration + 1))
    before="$(count_unchecked)"
    echo ""
    echo "=== iteration $iteration/$MAX_ITERATIONS [$mode mode] — unchecked tasks: $before ==="

    # -- fresh agent process, mode-appropriate prompt on stdin --------------
    build_prompt | bash -c "$AGENT_CMD" >"$LOG_DIR/iter-$iteration-agent.log" 2>&1
    agent_status=$?
    [ "$agent_status" -ne 0 ] && echo "loop: agent exited $agent_status (see $LOG_DIR/iter-$iteration-agent.log)" >&2

    # -- mechanical accounting: the runner measures, the agent is not trusted
    after="$(count_unchecked)"
    delta=$((before - after))
    last_verify_log="$LOG_DIR/iter-$iteration-verify.log"
    if run_verify; then verify_ok=1; else verify_ok=0; fi

    echo "loop: iteration $iteration — tasks checked off: $delta, verify: $([ "$verify_ok" -eq 1 ] && echo green || echo RED)"

    if [ "$mode" = "fix" ]; then
        [ "$delta" -ne 0 ] && echo "loop: WARNING — repair iteration touched the checklist (delta $delta); repairs must fix code only." >&2
        if [ "$verify_ok" -eq 1 ]; then
            echo "loop: verification repaired — returning to task mode."
            mode="task"
            fix_attempts=0
        else
            fix_attempts=$((fix_attempts + 1))
            echo "loop: verification still RED after repair attempt $fix_attempts/$MAX_FIX_ATTEMPTS" >&2
            if [ "$fix_attempts" -ge "$MAX_FIX_ATTEMPTS" ]; then
                echo "loop: $MAX_FIX_ATTEMPTS repair attempts without a green verification — aborting; fix interactively." >&2
                exit 5
            fi
            continue
        fi
    else
        [ "$delta" -gt 1 ] && echo "loop: WARNING — iteration checked off $delta tasks; prompt discipline is one task per iteration." >&2
        if [ "$verify_ok" -eq 0 ]; then
            echo "loop: verification RED — switching to repair mode; the loop will not move on until it is green." >&2
            mode="fix"
            fix_attempts=0
            continue
        fi
        if [ "$delta" -le 0 ] || [ "$agent_status" -ne 0 ]; then
            consecutive_failures=$((consecutive_failures + 1))
            echo "loop: no-progress task iteration ($consecutive_failures/$MAX_CONSECUTIVE_FAILURES consecutive)" >&2
            if [ "$consecutive_failures" -ge "$MAX_CONSECUTIVE_FAILURES" ]; then
                echo "loop: $MAX_CONSECUTIVE_FAILURES consecutive no-progress iterations — aborting instead of burning iterations." >&2
                exit 4
            fi
        else
            consecutive_failures=0
        fi
    fi

    # -- success: mechanical evidence only ----------------------------------
    if [ "$after" -eq 0 ] && [ "$verify_ok" -eq 1 ]; then
        echo ""
        echo "loop: SUCCESS — checklist complete and verification green after $iteration iteration(s)."
        echo "loop: next steps are human-gated: work-review, then openspec-archive-change."
        exit 0
    fi
done
