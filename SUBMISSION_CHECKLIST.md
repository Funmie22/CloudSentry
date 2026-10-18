# Submission Checklist

Use the local checks before publishing. Items marked **Manual** require your GitHub account or the hackathon portal; they have not been performed from this workspace.

## Local Project Checks

- [x] `app.py` contains the CLI and graph-backed investigation tools.
- [x] `test_app.py` contains offline tests and a FalkorDB integration test.
- [x] `README.md` explains setup, graph model, queries, and limitations.
- [x] `DEMO.md` contains a repeatable terminal walkthrough.
- [x] Mermaid diagrams use the labels, directed relationships, and query flow in the implementation.
- [x] `.gitignore` excludes Python cache/virtual-environment artifacts, `.env` files, and the local `app.py.bak` backup.
- [x] Run the test suite before publishing; all 10 tests passed locally, including the live FalkorDB integration test:

  ```powershell
  py -m unittest -v
  ```

- [x] Run the seed and demo commands from [DEMO.md](DEMO.md) with FalkorDB available.
- [ ] Review all staged files and the final Git diff; do not commit `.env` files, credentials, tokens, local logs, virtual environments, or `app.py.bak`.
- [ ] Run a secret scan appropriate to your tooling before publishing.

## GitHub Actions (Manual)

- [ ] Create or select the intended GitHub repository and verify its visibility setting is **Public**. Visibility is not verified by this project folder.
- [ ] Initialize Git if needed. This workspace currently has no `.git` directory, so Git status, remotes, commits, and `git diff --check` cannot currently be verified here.
- [ ] Review the repository contents and commit history before pushing. Include the app and submission documentation; do not include local backups or environment files.
- [ ] After initializing/configuring Git, run `git status --short`, review `git diff --check`, and inspect the staged diff before committing and pushing.
- [ ] Open the pushed README on GitHub and verify both Mermaid diagrams render.
- [ ] Record and review the complete terminal demo described in [DEMO.md](DEMO.md). No recording or screenshot is included in this workspace.

## Hackathon Portal (Manual)

- [ ] Complete the official submission form and provide the public repository URL.
- [ ] Add the demo recording link if the portal requires it.
- [ ] Confirm the portal accepted the submission before the deadline. No submission has been made from this workspace.