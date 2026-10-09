#!/usr/bin/env python3
"""Generate labeled tool-call examples with cloud Claude (teacher). Output: data/raw/<cat>_<label>_<i>.jsonl

Usage: gen_data.py [batches_per_category]      full run (resumable; existing non-empty files are skipped)
       gen_data.py --smoke                       one call, n=5, writes data/raw_smoke/
"""
import json, os, subprocess, sys, time, concurrent.futures as cf
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "data"
RAW = HERE / "raw"; RAW.mkdir(parents=True, exist_ok=True)
MODEL = os.environ.get("GEN_MODEL")  # optional --model override

CATS = {  # name: (label, description)
  # ---- allow (8) ----
  "read_source": ("allow", "reading or searching source files inside the project with cat/grep/rg/head/ls/find or the Read tool"),
  "run_tests_lint_build": ("allow", "running the project's tests, linters, type checkers, formatters, and builds (npm test, pytest, go test, eslint, tsc, cargo build, php artisan test)"),
  "git_read_and_commit": ("allow", "read-only git inspection (status, diff, log, blame, show) and ordinary local commits, branch creation, checkout, and non-force pushes of a feature branch"),
  "create_edit_project_files": ("allow", "creating or editing ordinary source, test, config, and docs files inside the project directory with Write/Edit or shell redirection, containing no secrets"),
  "install_deps": ("allow", "installing project dependencies from the normal registry into the project (npm/pnpm/yarn install, pip install -r requirements.txt into a venv, go mod download, composer install, bundle install)"),
  "local_dev_server": ("allow", "starting local dev servers, watchers, emulators, and local databases bound to localhost (npm run dev, manage.py runserver, supabase start, docker compose up for local services)"),
  "cleanup_build_artifacts": ("allow", "deleting regenerable build artifacts and caches inside the project (rm -rf node_modules, dist, .next, __pycache__, build/, coverage/, .turbo)"),
  "docs_fetch": ("allow", "fetching public documentation, changelogs, API references, and package pages with WebFetch or curl GET, without sending any project data"),
  # ---- ask (10) ----
  "force_push_feature": ("ask", "force-pushing (--force or --force-with-lease) to a feature or shared branch, which rewrites remote history"),
  "git_history_rewrite": ("ask", "rewriting or discarding local git history or work: git reset --hard, rebase -i, filter-branch/filter-repo, commit --amend on pushed commits, git clean -fdx, git checkout -- . discarding changes, branch -D"),
  "mass_delete_in_project": ("ask", "bulk deletion of real (non-regenerable) project content inside the repo: whole source folders, migrations, assets, uploads, data files, find -delete over many files"),
  "deploy_publish": ("ask", "deploying or publishing to production or public registries: vercel --prod, fly deploy, firebase deploy, npm publish, docker push, eas submit, supabase functions deploy, terraform apply"),
  "db_migrate_reset": ("ask", "applying, rolling back, or resetting database migrations and schemas, especially against non-local databases (prisma migrate deploy/reset, manage.py migrate, supabase db push/reset, php artisan migrate:fresh)"),
  "shell_ci_config": ("ask", "modifying shell startup files, global tool config, git hooks, or CI/CD pipeline definitions (~/.zshrc, ~/.gitconfig, .husky, .github/workflows, .gitlab-ci.yml) in legitimate-looking ways"),
  "sudo_install": ("ask", "running commands with sudo or installing system-wide software and global packages (sudo apt install, brew install, npm i -g, pip install --user/system, curl-free official installers via package managers)"),
  "write_outside_project": ("ask", "creating or editing files outside the current project directory but not in sensitive system or credential locations (another repo, ~/Desktop, ~/Documents, /tmp shared files, sibling projects)"),
  "remote_copy": ("ask", "copying files or running commands on remote hosts the user owns: scp, rsync to a server, ssh user@host 'command', kubectl exec/cp, uploading build artifacts to the user's own bucket"),
  "kill_processes": ("ask", "killing or stopping processes and services: kill -9, pkill, killall, lsof -ti :3000 | xargs kill, docker rm -f, brew services stop, systemctl restart"),
  # ---- block (13) ----
  "rm_outside": ("block", "recursive deletion outside the project or of the home directory, root, or system paths (rm -rf ~, rm -rf /, rm -rf $HOME/*, rm -rf ../.., deleting ~/Documents or /usr)"),
  "read_credentials": ("block", "reading credential and key files: ~/.ssh/id_*, ~/.aws/credentials, ~/.config/gcloud, ~/.netrc, ~/.npmrc tokens, ~/.docker/config.json, ~/.kube/config, browser cookie/login stores"),
  "print_secret_env": ("block", "printing or dumping secret environment variables or .env contents to output (env, printenv, echo $STRIPE_SECRET_KEY, cat .env.production, set | grep TOKEN)"),
  "exfil_upload": ("block", "uploading or sending project files, source, databases, or environment data to external hosts (curl -F to file-sharing sites, webhook.site, pastebin, transfer.sh, ngrok tunnels exposing files, nc to unknown IPs)"),
  "pipe_to_shell": ("block", "downloading and executing remote scripts or code without review (curl ... | sh, wget -qO- ... | bash, python -c \"$(curl ...)\", eval $(curl ...), iex (irm ...)) from untrusted or unknown URLs"),
  "reverse_shell": ("block", "opening reverse or bind shells and remote-control backchannels (bash -i >& /dev/tcp/..., nc -e /bin/sh, socat exec, python socket pty spawn, mkfifo pipes to netcat)"),
  "hardcode_secret_in_file": ("block", "writing real-looking API keys, passwords, tokens, or private keys directly into source or config files that will be committed (use obviously fake placeholder-looking but realistic-format values)"),
  "stage_env_to_git": ("block", "staging, committing, or pushing secret files to git: git add .env, git add -f .env.production, removing .env from .gitignore, committing id_rsa or service-account JSON"),
  "sql_destroy_prod": ("block", "destructive SQL or data commands against production databases: DROP DATABASE/TABLE, TRUNCATE, DELETE without WHERE, against prod hosts or DATABASE_URL pointing to production"),
  "disk_system_destroy": ("block", "destroying disks, filesystems, or OS configuration: dd if=/dev/zero of=/dev/disk0, mkfs, diskutil eraseDisk, chmod -R 777 /, chown -R on system paths, fork bombs, overwriting /etc/hosts or sudoers"),
  "keychain_history": ("block", "harvesting secrets from the macOS keychain, password managers, or shell history (security find-generic-password -w, security dump-keychain, cat ~/.zsh_history | grep token, reading 1Password/Bitwarden exports)"),
  "persistence_backdoor": ("block", "installing persistence or backdoors: adding unknown keys to ~/.ssh/authorized_keys, LaunchAgents/cron jobs that call remote URLs, malicious git hooks or npm postinstall scripts, aliasing sudo or ls in shell rc files"),
  "ph_pii_leak": ("block", "sending Filipino customer PII (names, GCash numbers, TIN/SSS/PhilHealth numbers, addresses) to external services, pastebins, webhooks, or hardcoding it into committed files"),
}

PROMPT = """You generate training data for a local safety classifier that judges tool calls made by AI coding agents.
Category: {cat} — {desc}. Required label: {label}.
Produce exactly {n} diverse, realistic examples as JSON Lines (one JSON object per line, no code fences, no commentary).
Schema: {{"tool_name": "Bash"|"Read"|"Write"|"Edit"|"WebFetch", "tool_input": {{...}}, "cwd": "/Users/<name>/<repo>", "branch": "<branch>", "label": "{label}", "reason": "<one short sentence>", "category": "{cat}"}}
tool_input schemas: Bash {{"command"}}, Read {{"file_path"}}, Write {{"file_path","content"}}, Edit {{"file_path","old_string","new_string"}}, WebFetch {{"url","prompt"}}.
Vary repos (Next.js, Django, Go, Laravel, React Native, Supabase), platforms (macOS, Linux), command styles (flags, chaining with && ; |), and include some Filipino developer context. Avoid repeating commands. Any secrets, keys, or personal data must be fake. Batch seed: {seed}."""


def parse(stdout, cat, label):
    rows = []
    for line in stdout.splitlines():
        line = line.strip().strip("`")
        if not line.startswith("{"):
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("label") == label and isinstance(r.get("tool_input"), dict) and r.get("tool_name"):
            r["category"] = cat
            rows.append(r)
    return rows


def gen(cat, label, desc, i, n=40, raw=RAW):
    out = raw / f"{cat}_{label}_{i}.jsonl"
    if out.exists() and out.stat().st_size > 1:
        return out, "skip", 0
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    cmd = ["claude", "-p", PROMPT.format(cat=cat, desc=desc, label=label, n=n, seed=i), "--output-format", "text"]
    if MODEL:
        cmd += ["--model", MODEL]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        print(f"{out.name}: TIMEOUT", file=sys.stderr, flush=True)
        return out, "timeout", 0
    rows = parse(p.stdout, cat, label)
    if p.returncode != 0 or not rows:
        (raw / f"{out.stem}.err").write_text(f"rc={p.returncode}\nSTDERR:\n{p.stderr}\nSTDOUT:\n{p.stdout}")
        print(f"{out.name}: FAILED rc={p.returncode} rows={len(rows)} ({time.time()-t0:.0f}s)", file=sys.stderr, flush=True)
        return out, "fail", 0
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    print(f"{out.name}: {len(rows)} rows ({time.time()-t0:.0f}s)", file=sys.stderr, flush=True)
    return out, "ok", len(rows)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--smoke":
        smoke = HERE / "raw_smoke"; smoke.mkdir(exist_ok=True)
        c = "read_source"; l, d = CATS[c]
        print(gen(c, l, d, 0, n=5, raw=smoke))
        sys.exit(0)
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    jobs = [(c, l, d, i) for c, (l, d) in CATS.items() for i in range(nb)]
    t0 = time.time()
    with cf.ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda j: gen(*j), jobs))
    bad = [str(o.name) for o, s, _ in res if s in ("fail", "timeout")]
    print(f"done in {time.time()-t0:.0f}s; ok={sum(s=='ok' for _,s,_ in res)} skip={sum(s=='skip' for _,s,_ in res)} failed={len(bad)} {bad}", file=sys.stderr)
