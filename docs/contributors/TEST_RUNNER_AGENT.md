# AutoYou Server Test Runner Agent

Purpose: execute the public validation contract mechanically and return concise
evidence to the requesting maintainer.

## Inputs

- Changed paths or the requested validation scope.
- Whether opt-in live-server tests are requested.

## Procedure

1. Read [TESTING_SKILL.md](TESTING_SKILL.md).
2. Select the smallest affected test suite.
3. Set an isolated `AUTOYOU_TEST_ROOT`.
4. Run the selected tests. Run opt-in live-server checks only when explicitly
   requested.
5. Run the worktree export check for release-facing changes.
6. Return pass/fail/skip results, command lines, and log locations. Do not
   edit source files unless separately asked to implement a fix.

The agent must stop and report if a test would require a real account,
credential, device, or production service.
