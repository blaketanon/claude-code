"""Read-only guard for the investigator's Bash tool.

Claude gets a real shell so it can run `git log`, `sf data query`, `aws logs filter-log-events`, `rg`…
This module decides, before each command runs, whether it is read-only. Deny by default: a
command is allowed only if every segment of the pipeline starts with an allow-listed program
(with per-program sub-rules) and there is no file redirection or command substitution.
"""
import re
import shlex

# Programs that only read, with no sub-rules.
PLAIN = {
    "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "find", "ls", "tree", "wc", "sort", "uniq",
    "cut", "awk", "tr", "diff", "comm", "column", "jq", "yq", "echo", "printf", "date", "basename", "dirname", "realpath",
    "pwd", "file", "stat", "du", "df", "which", "env", "true", "false", "test", "xargs", "tee", "nl", "paste", "fold",
    "md5sum", "sha256sum", "base64", "strings", "zcat", "gunzip", "tar", "unzip", "sleep", "whoami", "hostname", "uname",
}
# Programs that may read or write depending on arguments.
GIT_READ = {"log", "show", "diff", "blame", "grep", "status", "ls-files", "ls-tree", "rev-parse", "rev-list", "branch",
            "tag", "describe", "shortlog", "cat-file", "name-rev", "remote", "config", "for-each-ref", "merge-base", "whatchanged"}
GIT_READ_FLAGS_FORBIDDEN = {"--edit", "-e", "--unset", "--add", "--replace-all", "-d", "-D", "-m", "-M", "--delete", "--move", "set-url", "add", "rm", "rename"}
SF_READ_PREFIXES = (
    ("data", "query"), ("data", "get", "record"), ("data", "search"), ("data", "export", "tree"),
    ("sobject", "describe"), ("sobject", "list"), ("org", "display"), ("org", "list"), ("org", "list", "limits"), ("org", "list", "metadata"),
    ("apex", "list", "log"), ("apex", "get", "log"), ("api", "request", "rest"), ("project", "retrieve", "preview"),
    ("force:data:soql:query",), ("force:org:display",), ("limits", "api", "display"), ("version",), ("--version",), ("plugins",),
)
AWS_READ_VERBS = ("describe-", "get-", "list-", "filter-", "lookup-", "tail", "start-query", "stop-query", "batch-get-", "query", "scan", "head-", "select-", "search-")
AWS_DENIED_SERVICES = {"iam", "sts-assume", "kms", "secretsmanager", "ssm"}   # read access here would expose secrets
PSQL_WRITE = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|copy|grant|revoke|vacuum|reindex|call|do)\b", re.I)

REDIRECT = re.compile(r"(?<![0-9&])>(?!&[12])|>>|<\(|\$\(|`")   # file redirections, substitution


class Verdict:
    __slots__ = ("allowed", "reason")

    def __init__(self, allowed, reason=""):
        self.allowed = allowed
        self.reason = reason

    def __bool__(self):
        return self.allowed


def check_readonly(command, allow_psql=False):
    cmd = (command or "").strip()
    if not cmd:
        return Verdict(False, "empty command")
    if "\n" in cmd and not cmd.rstrip().endswith("'") and "<<" in cmd:
        return Verdict(False, "heredocs are not allowed; pass arguments inline")
    stripped = _strip_quoted(cmd)
    if REDIRECT.search(stripped):
        return Verdict(False, "file redirection, command substitution and backticks are not allowed (read-only shell)")
    for seg in _segments(stripped, cmd):
        v = _check_segment(seg, allow_psql)
        if not v:
            return v
    return Verdict(True)


def _strip_quoted(cmd):
    """Blank out the contents of quoted strings so operators inside SOQL / regexes don't trip the checks."""
    out, i, q = [], 0, None
    while i < len(cmd):
        c = cmd[i]
        if q:
            if c == "\\" and q == '"':
                i += 2
                out.append("  ")
                continue
            if c == q:
                q = None
                out.append(c)
            else:
                out.append(" ")
        else:
            if c in "'\"":
                q = c
            out.append(c)
        i += 1
    return "".join(out)


def _segments(stripped, original):
    """Split on pipeline / list operators using positions found in the quote-stripped copy."""
    parts, last = [], 0
    for m in re.finditer(r"\|\||&&|\||;|\n", stripped):
        parts.append(original[last:m.start()])
        last = m.end()
    parts.append(original[last:])
    return [p.strip() for p in parts if p.strip()]


def _check_segment(seg, allow_psql):
    try:
        argv = shlex.split(seg, posix=True)
    except ValueError as e:
        return Verdict(False, f"could not parse command: {e}")
    # skip leading env assignments and common wrappers
    while argv and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[0]):
        argv = argv[1:]
    while argv and argv[0] in ("time", "nice", "timeout", "command", "env", "nohup"):
        argv = argv[1:]
        if argv and argv[0].startswith("-") and argv[0] not in ("--",):
            argv = argv[1:]
        if argv and argv and re.match(r"^\d+[smh]?$", argv[0]):
            argv = argv[1:]
    if not argv:
        return Verdict(True)
    prog = argv[0].rsplit("/", 1)[-1]
    args = argv[1:]

    if prog in PLAIN:
        if prog == "tee":
            return Verdict(False, "tee writes files")
        if prog == "tar" and args and any(ch in args[0].lstrip("-").split("=")[0] for ch in "xcru") and not args[0].startswith("--"):
            return Verdict(False, "tar extract/create writes files")
        if prog == "unzip" and "-l" not in args and "-p" not in args:
            return Verdict(False, "unzip without -l/-p writes files")
        if prog == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir") for a in args):
            return Verdict(False, "find -delete/-exec is not allowed")
        if prog == "xargs":
            return _check_segment(" ".join(shlex.quote(a) for a in args if not a.startswith("-")), allow_psql)
        return Verdict(True)
    if prog == "sed":
        if any(a == "-i" or a.startswith("-i") or a == "--in-place" for a in args):
            return Verdict(False, "sed -i edits files")
        return Verdict(True)
    if prog == "git":
        sub, skip = None, False
        for a in args:
            if skip:
                skip = False
                continue
            if a in ("-C", "-c", "--git-dir", "--work-tree"):
                skip = True
                continue
            if a.startswith("-"):
                continue
            sub = a
            break
        if sub not in GIT_READ:
            return Verdict(False, f"git {sub or ''} is not read-only")
        if any(a in GIT_READ_FLAGS_FORBIDDEN for a in args):
            return Verdict(False, "that git flag modifies the repository")
        if sub == "config" and not any(a in ("--get", "--list", "-l", "--get-all", "--get-regexp") for a in args):
            return Verdict(False, "git config is only allowed with --get/--list")
        return Verdict(True)
    if prog in ("sf", "sfdx"):
        words = tuple(a for a in args if not a.startswith("-"))
        for pref in SF_READ_PREFIXES:
            if words[:len(pref)] == pref:
                return Verdict(True)
        return Verdict(False, f"sf {' '.join(words[:3])} is not in the read-only allow-list")
    if prog == "aws":
        words = [a for a in args if not a.startswith("-")]
        if len(words) < 2:
            return Verdict(True) if words and words[0] in ("help", "--version") else Verdict(False, "aws needs a service and operation")
        service, op = words[0], words[1]
        if service in AWS_DENIED_SERVICES:
            return Verdict(False, f"aws {service} is off limits (secrets)")
        if service == "sts" and op == "get-caller-identity":
            return Verdict(True)
        if service == "s3" and op in ("ls",):
            return Verdict(True)
        if service == "logs" and op == "tail":
            return Verdict(True)
        if any(op.startswith(v) or op == v.rstrip("-") for v in AWS_READ_VERBS):
            return Verdict(True)
        return Verdict(False, f"aws {service} {op} is not a read operation")
    if prog == "psql":
        if not allow_psql:
            return Verdict(False, "psql is not enabled (set READONLY_DATABASE_URLS on the server)")
        joined = " ".join(args)
        if PSQL_WRITE.search(joined) or "-f" in args:
            return Verdict(False, "only SELECT statements are allowed through psql")
        return Verdict(True)
    if prog in ("python3", "python", "node", "bash", "sh", "zsh", "perl", "ruby"):
        return Verdict(False, f"{prog} could do anything; use the read-only tools instead")
    if prog == "curl" or prog == "wget":
        return Verdict(False, "network fetches are not allowed from the shell")
    return Verdict(False, f"{prog} is not on the read-only allow-list")


# ---- fixer: a looser guard. Edits happen through Claude's Edit/Write tools; the shell is for running
# tests, builds and git inspection, never for pushing or touching anything outside the repo.
FIX_DENY = re.compile(
    r"\bgit\s+(push|remote\s+add|remote\s+set-url|reset\s+--hard\s+origin|filter-branch|config\s+--global)\b|"
    r"\b(sf|sfdx)\s+(project\s+deploy|data\s+(create|update|delete|upsert|import)|apex\s+run|org\s+(create|delete|login))\b|"
    r"\baws\s+\S+\s+(put|create|update|delete|terminate|modify|run|start|stop|invoke|deploy|publish|restart|reboot)-?|"
    r"\b(eb|zappa|serverless|sls|terraform|cdk|pulumi|kubectl|helm|docker\s+push)\b|"
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+(/|~|\$HOME|\.\.)|\bmkfs\b|\bdd\s+if=|\bchmod\s+-R\s+777\b|"
    r"\bcurl\b.*\s-(X\s*)?(POST|PUT|DELETE|PATCH)\b|\bpip\s+install\b.*\s--user\b|\bnpm\s+publish\b|\bgem\s+push\b",
    re.I,
)


def check_fix_command(command):
    cmd = (command or "").strip()
    if FIX_DENY.search(cmd):
        return Verdict(False, "that command deploys, pushes, or changes shared infrastructure; the worker pushes and opens the PR itself")
    return Verdict(True)
