# Getting your existing Replit project into this repository

The project is over the upload limit and the Replit workspace is disconnected,
but the code is still there. Replit keeps your files when a Repl is not running;
only the always-on hosting was costing money. Pushing to GitHub is free and does
not require the Repl to be running or published.

## The straightforward route: push from Replit to GitHub

1. Open [replit.com](https://replit.com) and open the Repl. It will boot into the
   editor. You do not need to run anything.
2. Open the **Git** panel in the left sidebar (the branch icon). On newer
   accounts this is under **Tools → Git**.
3. Choose **Connect to GitHub** and authorise Replit if prompted.
4. Create a new repository, or connect to this one. If it offers to create one,
   name it something like `forge-os-legacy`.
5. Stage everything, write a commit message, and push.

If the Git panel is missing, use the Shell tab instead:

```bash
# in the Replit shell
git init                       # only if it is not already a repository
git add -A
git commit -m "Existing Forge OS work from Replit"
git branch -M main
git remote add origin https://github.com/<your-username>/forge-os-legacy.git
git push -u origin main
```

GitHub will ask for a personal access token rather than a password. Create one at
**GitHub → Settings → Developer settings → Personal access tokens → Fine-grained
tokens**, give it read and write access to that one repository, and paste it as
the password.

Then tell me the repository name and I can read it directly.

## If the repository is large

Over 30MB usually means build output, dependencies or assets are being committed
rather than source. Before pushing, add a `.gitignore`:

```
node_modules/
.next/
dist/
build/
__pycache__/
*.pyc
.venv/
venv/
.cache/
.upm/
attached_assets/
*.sqlite
*.db
.env
```

Then `git rm -r --cached node_modules .next dist build` before committing. In most
Replit projects this takes a 200MB directory down to a few megabytes of actual
code, which is all I need.

If a large file is genuinely part of the project, use Git LFS, or leave it out and
describe it to me instead.

## If GitHub is not an option

Any of these work:

- Zip only the source directories, with dependencies excluded, and upload that.
  Excluding `node_modules` almost always brings it under the limit.
- Paste the important files directly into our conversation. If the project is
  mostly one application, the schema, the main routes and the core logic are
  usually enough for me to work with.
- Tell me the structure and the stack, and what does and does not work. I can
  often tell you what is worth migrating without reading every line.

## What I would actually want from it

Be selective. Most of a half-built project is scaffolding that this repository
already has in better shape. What is worth carrying over:

1. **Anything encoding real accounting judgement.** Chart-of-account mappings,
   categorisation rules, client-specific treatments, tax logic you have already
   validated. This is the expensive part and it does not depend on framework
   choices.
2. **Working integrations.** If you already have QuickBooks Online OAuth working
   end to end, that is weeks of fiddly work and it should be lifted, not rewritten.
3. **Client-facing copy and positioning.** Anything you have written that already
   lands with a contractor.
4. **Real data samples.** Even one anonymised client file with known problems is
   worth more to ForgeBench than a month of synthetic generation.

What is probably not worth carrying over: UI scaffolding, auth, generic CRUD, and
any prompt-only agent logic. Those are cheap to rebuild correctly and expensive
to untangle.

## Running this repository on your own machine

You do not need Claude Code installed to use what is here.

```bash
git clone https://github.com/amroellithyw-boop/New-Website.git
cd New-Website/services/forge-core
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

forge tieout
forge bench
forge review --seeded --out review.html
```

Then open `review.html` in a browser. That file is the artefact you sell.
