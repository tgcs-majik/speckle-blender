# Tests

Three layers covering the TGCS publish-progress changes.

| Layer | What it covers | Needs Blender? | Needs a server? |
|-------|----------------|:--------------:|:---------------:|
| `unit/` | `ProgressServerTransport` counter, clamped %, error-swallowing, console cadence | no (`fake-bpy`) | no |
| `integration/` | the transport streaming a real object tree to a real Speckle server + round-trip | no | yes |
| `e2e/` | real cube meshes → conversion → streamed upload → `version.create` inside Blender | yes | yes |

## Unit + integration (pytest, via uv)

```sh
uv sync                       # creates .venv with specklepy + fake-bpy + pytest
uv run pytest tests/unit      # offline, always runs

# integration talks to a server; skipped unless these are set:
SPECKLE_TOKEN=<token> SPECKLE_PROJECT=<projectId> \
  uv run pytest tests/integration
```

## E2e (inside headless Blender)

The connector must be built + installed first (`blender --command extension
build/install-file`). Then:

```sh
SPECKLE_TOKEN=<token> SPECKLE_ACCOUNT_ID=<accountId> SPECKLE_PROJECT=<projectId> \
  blender --background --python tests/e2e/test_publish_e2e.py
```

Prints `E2E_PASS version=<id>` and exits 0 on success; exits 1 on any failure.
It creates and deletes its own throwaway model, so it leaves no residue.
