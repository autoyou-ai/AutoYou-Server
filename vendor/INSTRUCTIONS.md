# Optional vendored dependency instructions

This public source release does not vendor Cognee. Do not copy a Cognee
checkout, virtual-environment package, model, cache, or runtime state from
another machine or repository into this directory.

For the optional Cognee memory backend, use the exact public upstream and
revision pinned in [`requirements/cognee.txt`](../requirements/cognee.txt):

```powershell
git clone https://github.com/topoteretes/cognee.git vendor/cognee
git -C vendor/cognee checkout 1913271821c84cec1630dd5b15ceb17dee8ace55
```

For a supported Python environment, prefer the pinned requirement instead:

```powershell
python -m pip install -r requirements/cognee.txt
```

`vendor/cognee/` is local-only: it is excluded from Docker build context and
the public-source export. Keep it free of credentials, downloaded models,
caches, and runtime data. Tests must use an `AUTOYOU_TEST_ROOT` temporary
directory and must not modify a live setup.
