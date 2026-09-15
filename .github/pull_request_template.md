## What this changes

## Why

## Control impact
<!-- New or modified controls, and what they now detect that they did not before. -->

## ForgeBench
<!-- Paste the output of `forge bench`. Critical recall must not regress. -->

```
```

## Tie-out
<!-- Paste the output of `forge tieout`. -->

```
```

## Risk and rollback
<!-- What breaks if this is wrong, and how it is reverted. -->

## Checklist
- [ ] Money arithmetic stays in `forge.money`; no floats
- [ ] New findings carry evidence references and calculations
- [ ] Autonomy boundaries unchanged, or the change is called out explicitly
- [ ] Tests added for the failure mode, not just the happy path
