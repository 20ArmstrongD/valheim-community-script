# Publish your development repository on GitHub

This is a standalone MIT-licensed development repository. Publishing it does not
deploy the application, touch a Proxmox host, or open a pull request upstream.
Keep its development status visible until fresh-LXC acceptance tests pass.

## Option 1: GitHub CLI

From this repository's directory, authenticate using GitHub's browser flow:

```bash
gh auth login --hostname github.com --git-protocol https --web
```

Then create a public repository and push the local `main` branch:

```bash
gh repo create valheim-community-script \
  --public \
  --source=. \
  --remote=origin \
  --description "Development candidate: Valheim dedicated server installer for an unprivileged Proxmox LXC using Community Scripts Core" \
  --push
```

Use `--private` instead of `--public` if you want to review it privately first.
These commands use the account selected by `gh auth login`. Do not paste access
tokens into the terminal command, repository files, issue reports or chat.

## Option 2: GitHub website

1. Visit <https://github.com/new> and name the repository
   `valheim-community-script` (or choose another name).
2. Select public or private. Leave initialization options unchecked: this local
   repository already includes a README, license and `.gitignore`.
3. After creation, copy the HTTPS URL from GitHub and run:

   ```bash
   git remote add origin https://github.com/YOUR_USERNAME/valheim-community-script.git
   git push -u origin main
   ```

Replace `YOUR_USERNAME` and the repository name with the ones you created.
Authenticate through your credential manager/GitHub CLI if prompted; GitHub
does not accept account passwords for HTTPS Git operations.

## After publishing

Check the Actions tab for the validation workflow. It runs tests, ShellCheck and
systemd unit verification on an isolated runner; it does not create an LXC or
start a Valheim server. A green workflow does not establish gameplay compatibility.

Keep [the real-host test checklist](testing.md) and [submission notes](submission.md)
with the project. A future contribution to DevScripts is a separate branch/PR
against that project's repository and needs its own review and approval.

The local repository uses a GitHub noreply commit address. Confirm your GitHub
Settings → Emails if you want commits associated with your account; GitHub may
provide an ID-prefixed noreply address you can use for future commits:

```bash
git config user.email 'YOUR_GITHUB_NOREPLY_ADDRESS'
```

This setting applies only to this repository. Never commit real configuration,
credentials, world saves or archives; the supplied ignore rules cover common
forms, but review `git diff --cached` before each commit.
