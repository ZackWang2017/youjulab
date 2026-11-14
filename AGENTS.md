# Desktop Development Workflow

- After completing requested application changes, rebuild, install, and restart the local macOS desktop app by default, unless the user explicitly requests otherwise.
- Use `npm run rebuild:install:mac -- --yes` in `apps/office-desktop`; this rebuilds Web and the Python sidecar, verifies the result, replaces `/Applications/youjulab.app`, and restarts it.
- Do not create an old App backup. Preserve user data and do not restart independent MCP or admin services.
- If a normal App exit request fails, retry normal exit once before asking the user; do not force termination or overwrite a running installation.
- Wait for the script to finish and report any build, installation, or startup failure accurately.
