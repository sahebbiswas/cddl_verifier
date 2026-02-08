# Publish Wiki to GitHub

GitHub wikis are stored in a separate repository: `<repo>.wiki.git`.

Common workflow to publish these pages:

1. Clone the wiki repo locally (replace `OWNER/REPO`):

```bash
git clone https://github.com/OWNER/REPO.wiki.git
cd REPO.wiki
```

2. Copy the Markdown files from this repository's `wiki/` folder into the cloned wiki repo directory.

3. Commit and push:

```bash
git add .
git commit -m "Update wiki pages"
git push origin main
```

Alternative using the `gh` CLI:

```bash
gh repo clone OWNER/REPO.wiki
# then copy files, commit, push
```

Notes:
- Replace `OWNER/REPO` with your repository owner/name.
- If your organization uses a different default branch for the wiki, use that branch name instead of `main`.

If you want, I can prepare a script to copy these files into a cloned wiki repo for you — but I can't push to the remote without your credentials.
