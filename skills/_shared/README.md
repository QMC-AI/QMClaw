# _shared/ — Common content for QMClaw skills

This directory **is not a skill**. It has no `SKILL.md` and is not registered with the plugin loader. It exists so multiple skills can reference the same content without duplication.

Files here are referenced by sibling skills via relative paths, for example:

```yaml
always_load:
  - ../_shared/core/quantum-params.md
```

## Current contents

| File | Used by |
|------|---------|
| `core/quantum-params.md` | All quantum calibration skills |
| `core/quality-standards.md` | QMClaw data analysis, calibration |
| `core/terminology.md` | All skills |

## When to add a file here

Only when ≥ 2 skills need the same content. If only one skill needs it, keep it inside that skill's `static/`.

## Design Principles

1. **Definitions over actions**: The shared layer holds definitions, reference material, and terminology.
2. **Action stays local**: The action layer — how a specific skill diagnoses, calibrates, or analyzes — stays inside each skill's `static/fragments/`.
3. **Consistent terminology**: All skills should use terms consistently as defined in `core/terminology.md`.