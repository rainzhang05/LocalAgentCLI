# LocalAgentCLI — Safety and Permissions

This document defines the safety system: approval modes, workspace boundaries, high-risk action rules, the approval UX, and the rollback mechanism. For tool-level safety classification, see [tool-system.md](tool-system.md).

---

## Overview

The safety system is the gatekeeper between the agent's intent and actual execution. Every tool call that modifies state passes through the Safety Layer, which decides whether to auto-approve, prompt the user, or block the action entirely. The system defaults to a cautious posture and requires explicit user action to increase autonomy.

---

## Approval Modes

### Balanced (Default)

The default mode balances productivity with safety.

| Action Type | Approval Required |
|---|---|
| File reads (`file_read`, `file_search`, `directory_list`) | No — auto-approved |
| Git status/diff (`git_status`, `git_diff`) | No — auto-approved |
| File writes (`file_write`, `patch_apply`) | Yes — prompt user |
| Shell commands (`shell_execute`) | Yes — prompt user |
| Test execution (`test_execute`) | Yes — prompt user |
| Git commits (`git_commit`) | Yes — prompt user |
| High-risk actions (see below) | Always — cannot be auto-approved |

### Autonomous (via `/agent approve`)

When the user issues `/agent approve` during an agent task, the current task switches to autonomous mode:
- Standard actions (file writes, shell commands, tests, git commits) are auto-approved for the duration of the current task
- High-risk actions still require explicit approval
- Autonomy resets when the task completes or the user issues `/agent stop`
- This mode does not persist across sessions

### Future Modes (Extensible)

The approval mode system should be designed to support additional modes in the future:
- **Strict**: Require approval for all actions including reads
- **Custom**: Per-tool approval settings

---

## Approval UX

### Inline Prompt

When a tool call requires approval, the system displays an inline prompt:

```
🔧 patch_apply: src/main.py
   Replace: "def old_function():"
   With:    "def new_function():"

   [Enter] Approve  |  [d] Deny  |  [v] View full diff
```

- **Enter (default)**: Approve and execute the tool call
- **d**: Deny the action. The agent receives a `denied` status and re-plans.
- **v**: Show a full preview of the change before deciding

For shell commands:
```
🔧 shell_execute: npm install express
   Working dir: /project

   [Enter] Approve  |  [d] Deny
```

### `/agent approve` Mode

When autonomous mode is active, the prompt is skipped for standard actions. The activity log still shows what was executed:

```
✓ patch_apply: src/main.py (auto-approved)
✓ shell_execute: npm test (auto-approved)
⚠ shell_execute: rm -rf /tmp/data (HIGH RISK — approval required)
   [Enter] Approve  |  [d] Deny
```

---

## Workspace Boundary

### Strict Root Enforcement

All tool operations are confined to the workspace root directory (set via `/workspace set <path>` or defaulting to the current directory at launch).

**Rules:**
1. All file paths are resolved relative to the workspace root
2. Paths that resolve outside the workspace are rejected (e.g., `../../../etc/passwd`)
3. Symlinks that point outside the workspace are rejected
4. Shell commands run with `cwd` set to the workspace root
5. Shell commands that attempt to `cd` outside the workspace are not blocked by the tool itself (since we can't reliably parse all shell commands), but the user is warned if the command references paths outside the workspace

### Path Validation

```python
# localagentcli/safety/boundary.py

class WorkspaceBoundary:
    def __init__(self, workspace_root: Path):
        self._root = workspace_root.resolve()

    def validate_path(self, path: str) -> Path:
        """Resolve a path and verify it's within the workspace.

        Returns the resolved absolute path if valid.
        Raises WorkspaceBoundaryError if the path escapes the workspace.
        """
        resolved = (self._root / path).resolve()
        if not resolved.is_relative_to(self._root):
            raise WorkspaceBoundaryError(
                f"Path '{path}' resolves to '{resolved}' which is outside "
                f"the workspace root '{self._root}'"
            )
        return resolved

    def validate_symlink(self, path: Path) -> None:
        """Check that a symlink target is within the workspace."""
        if path.is_symlink():
            target = path.resolve()
            if not target.is_relative_to(self._root):
                raise WorkspaceBoundaryError(
                    f"Symlink '{path}' points to '{target}' outside the workspace"
                )
```

### Outside-Workspace Access

If the agent or user explicitly needs to access a path outside the workspace:
1. The Safety Layer blocks the action with a clear message
2. The user can explicitly approve the out-of-workspace access for that specific action
3. The approval does not persist — each out-of-workspace access requires individual approval

---

## High-Risk Actions

These actions **always** require explicit user approval, even in autonomous mode:

| Action | Why It's High-Risk |
|---|---|
| Delete operations (`rm`, `rmdir`, file deletion) | Destructive and potentially irreversible |
| System-wide commands (e.g., `sudo`, `systemctl`, modifying `/etc`) | Affects the entire system, not just the workspace |
| External downloads (`curl`, `wget`, `pip install`, `npm install`) | Introduces external code/data |
| Credential access (reading `.env`, secrets, keys) | Sensitive data exposure |
| Git force operations (`git push --force`, `git reset --hard`) | Can destroy remote or local history |

### Detection Logic

The Safety Layer inspects tool calls to detect high-risk patterns:

```python
# localagentcli/safety/layer.py

class SafetyLayer:
    HIGH_RISK_COMMANDS = [
        r'\brm\b', r'\brmdir\b', r'\bsudo\b', r'\bsystemctl\b',
        r'\bcurl\b', r'\bwget\b', r'\bpip\s+install\b',
        r'\bnpm\s+install\b', r'\bgit\s+push\s+--force\b',
        r'\bgit\s+reset\s+--hard\b',
    ]

    HIGH_RISK_FILE_PATTERNS = [
        r'\.env$', r'\.pem$', r'\.key$', r'credentials',
        r'secrets?\.(json|yaml|yml|toml)$',
    ]

    def classify_risk(self, tool_name: str, args: dict) -> RiskLevel:
        """Classify a tool call as normal or high-risk."""
        if tool_name == "shell_execute":
            command = args.get("command", "")
            for pattern in self.HIGH_RISK_COMMANDS:
                if re.search(pattern, command):
                    return RiskLevel.HIGH
        if tool_name in ("file_read", "file_write", "patch_apply"):
            path = args.get("path", "")
            for pattern in self.HIGH_RISK_FILE_PATTERNS:
                if re.search(pattern, path):
                    return RiskLevel.HIGH
        return RiskLevel.NORMAL
```

---

## Rollback System

The safety system maintains rollback capability for all file modifications.

### File Backup

Before any file-modifying tool executes, the Safety Layer:
1. Checks if the target file exists
2. If it does, copies its current contents to a backup location
3. Records the backup in the rollback log

### Backup Storage

```
~/.localagent/cache/rollback/
├── <session-id>/
│   ├── 001_src_main.py          # Backup of src/main.py before first edit
│   ├── 002_src_utils.py         # Backup of src/utils.py before second edit
│   └── rollback_log.json        # Ordered log of all changes
```

### Rollback Log Schema

```json
{
  "session_id": "abc123",
  "entries": [
    {
      "index": 1,
      "timestamp": "2025-01-15T10:30:05Z",
      "tool": "patch_apply",
      "file_path": "src/main.py",
      "backup_path": "~/.localagent/cache/rollback/abc123/001_src_main.py",
      "action": "modified",
      "summary": "Replaced old_function with new_function"
    },
    {
      "index": 2,
      "timestamp": "2025-01-15T10:30:10Z",
      "tool": "file_write",
      "file_path": "src/new_module.py",
      "backup_path": null,
      "action": "created",
      "summary": "Created new module"
    }
  ]
}
```

### Undo Capability

The RollbackManager supports undoing changes in reverse order:

```python
# localagentcli/safety/rollback.py

class RollbackManager:
    def __init__(self, session_id: str, storage_path: Path):
        self._session_id = session_id
        self._storage = storage_path / "rollback" / session_id
        self._log: list[RollbackEntry] = []

    def backup_file(self, file_path: Path) -> None:
        """Create a backup of the file before modification."""

    def record_creation(self, file_path: Path) -> None:
        """Record that a new file was created (undo = delete)."""

    def undo_last(self) -> RollbackEntry:
        """Undo the most recent change. Restores the backup or deletes the created file."""

    def undo_all(self) -> list[RollbackEntry]:
        """Undo all changes in reverse order."""

    def get_history(self) -> list[RollbackEntry]:
        """Return the rollback log for review."""
```

### Rollback Rules

1. Backups are per-session. When a session ends, rollback data is retained for a configurable period (default: 24 hours)
2. Undo operates in strict reverse order — you cannot undo step 3 without first undoing steps 5, 4
3. If a file was modified multiple times, each modification has its own backup. Undoing restores to the state before that specific modification
4. Created files (no prior backup) are deleted on undo
5. Rollback data is stored in `~/.localagent/cache/rollback/` and cleaned up by the storage manager

---

## SafetyLayer Interface

```python
# localagentcli/safety/layer.py

class SafetyLayer:
    def __init__(self, approval_manager: ApprovalManager,
                 boundary: WorkspaceBoundary,
                 rollback: RollbackManager):
        self._approval = approval_manager
        self._boundary = boundary
        self._rollback = rollback

    def check_and_approve(self, tool: Tool, args: dict) -> ApprovalResult:
        """Full safety check pipeline:
        1. Validate paths against workspace boundary
        2. Classify risk level
        3. Check approval mode
        4. Prompt user if needed
        5. Create backup if approved
        Returns ApprovalResult (approved/denied/blocked).
        """

    def pre_action(self, tool: Tool, args: dict) -> None:
        """Called after approval, before execution. Creates file backups."""

    def post_action(self, tool: Tool, args: dict, result: ToolResult) -> None:
        """Called after execution. Updates rollback log."""
```

### ApprovalManager

```python
# localagentcli/safety/approval.py

class ApprovalManager:
    def __init__(self, mode: str = "balanced"):
        self._mode = mode  # "balanced" | "autonomous"

    def needs_approval(self, tool: Tool, risk_level: RiskLevel) -> bool:
        """Determine if the tool call needs user approval given current mode and risk."""
        if risk_level == RiskLevel.HIGH:
            return True  # Always for high-risk
        if self._mode == "autonomous":
            return False  # Auto-approve standard actions
        if tool.is_read_only:
            return False  # Never for reads
        return True  # Default: ask

    def set_autonomous(self) -> None:
        """Switch to autonomous mode for the current task."""
        self._mode = "autonomous"

    def reset(self) -> None:
        """Reset to default balanced mode."""
        self._mode = "balanced"
```
