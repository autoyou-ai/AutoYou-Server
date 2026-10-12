# AutoYou Server Agent Contract

Use this contract for any coding assistant working in this repository.

1. Read [LLM.txt](LLM.txt), [CONTRIBUTING.md](../../CONTRIBUTING.md), and
   [TESTING.md](TESTING.md) before editing.
2. Keep changes scoped to the requested server behavior and released source.
3. Do not add credentials, customer data, runtime state, package artifacts, or
   unreviewed binaries.
4. Keep tests isolated with `AUTOYOU_TEST_ROOT`; use synthetic fixtures only.
5. Run the smallest relevant tests, then run the export-boundary check for any
   release-facing change.
6. Report commands run, results, and any remaining owner or legal gates.

Do not change official-build authorization, signing, licensing, or release
controls unless the task explicitly authorizes that work.
