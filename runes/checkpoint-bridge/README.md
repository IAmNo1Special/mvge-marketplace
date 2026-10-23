# checkpoint-bridge

Shadow-git workspace checkpoints, atomic leaf restoration, and file rollbacks for MvgeOS.

## Features
- **Deterministic Checkpoints**: Snapshots workspace state across turns using lightweight shadow git trees.
- **Rollback Spells**: Reverts file modifications or whole workspace trees to any prior checkpoint.
- **Atomic Leaf Restoration**: Restores state safely without corrupting working directory uncommitted edits.
