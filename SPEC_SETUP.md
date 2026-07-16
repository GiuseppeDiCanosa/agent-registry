Setup complete. Remaining steps:

1. Enable pre-commit locally:
   pip install pre-commit && pre-commit install

2. On GitHub → Settings → Branches → Add rule for "main":
   ✓ Require status checks to pass before merging
   ✓ Require branches to be up to date before merging
   ✓ Status check: "Run spec verification"

3. Start your first change with openspec-propose (or openspec-explore), then
   openspec-apply-change to implement it, then spec-verify to check it.
