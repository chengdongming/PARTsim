# Simulator system templates

The two system YAML templates were moved from the repository root without
changing their contents. New CLI calls should use the paths in this directory.
The shared config and taskset-store code resolves the historical root-relative
logical names to these files; the original names remain in existing config
identity material to preserve taskset generation and seed identities.
