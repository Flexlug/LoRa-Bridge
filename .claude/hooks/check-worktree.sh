#!/usr/bin/env bash
# Блокирует Edit/Write/NotebookEdit в основном checkout'е.
# Обход (срочный фикс в main): touch .claude/worktree-bypass
# Снять обход:                  rm .claude/worktree-bypass

if [ -f ".claude/worktree-bypass" ]; then
  exit 0
fi

git_dir=$(git rev-parse --git-dir 2>/dev/null) || exit 0
git_common_dir=$(git rev-parse --git-common-dir 2>/dev/null) || exit 0

abs_git=$(realpath "$git_dir" 2>/dev/null || echo "$git_dir")
abs_common=$(realpath "$git_common_dir" 2>/dev/null || echo "$git_common_dir")

if [ "$abs_git" = "$abs_common" ]; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"⛔ Прямая запись в main запрещена. Создай worktree (EnterWorktree). Для срочного обхода: touch .claude/worktree-bypass"}}'
fi
