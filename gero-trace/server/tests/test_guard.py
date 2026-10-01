import pytest

from gero_trace.guard import check_fix_command, check_readonly

ALLOWED = [
    "git log --oneline -20 -- app/callback.py",
    "git -C repos/FM_Model8 log --since='2 days ago' --stat",
    "git blame -L 10,40 app/callback.py",
    "rg -n 'Amount__c' repos/ | head -50",
    "grep -rn \"StageName\" salesforce/force-app/main/default/classes",
    "cat repos/brokerd/README.md",
    "sf data query --target-org trace -q \"SELECT Id, Field, OldValue, NewValue FROM OpportunityFieldHistory WHERE OpportunityId = '006xx' ORDER BY CreatedDate\" --json",
    "sf apex list log --target-org trace --json",
    "sf apex get log -i 07Lxx --target-org trace",
    "sf sobject describe -s Opportunity --target-org trace --json | jq '.fields[].name'",
    "aws logs filter-log-events --log-group-name /aws/elasticbeanstalk/Gero-DevLog/var/log/web.stdout.log --start-time 1700000000000 --filter-pattern 'ERROR'",
    "aws logs tail /aws/lambda/fundrone --since 1h",
    "aws elasticbeanstalk describe-environments --application-name Gero_DevLog",
    "aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=UpdateEnvironment",
    "aws dynamodb query --table-name deals --key-condition-expression 'pk = :p' --expression-attribute-values '{\":p\":{\"S\":\"x\"}}'",
    "ls -la repos && find repos/brokerd -name '*.py' | wc -l",
    "TZ=America/New_York date -d @1700000000",
    "sed -n '1,80p' repos/brokerd/app.py",
    "git show abc123:app/callback.py | head -100",
    "echo 'SELECT 1 > 0' | cat",            # '>' inside quotes is fine
    "rg 'amount\\s*>\\s*0' repos/",         # '>' inside quotes is fine
    "git log -S'Amount' --oneline",
]

DENIED = [
    "rm -rf repos",
    "git push origin main",
    "git checkout -b x",
    "git fetch",
    "git commit -am x",
    "sf data update record -s Opportunity -i 006xx -v 'Amount=1' --target-org trace",
    "sf apex run --file x.apex",
    "sf project deploy start",
    "sf project retrieve start --manifest package.xml",
    "aws elasticbeanstalk update-environment --environment-name x",
    "aws lambda invoke --function-name x out.json",
    "aws secretsmanager get-secret-value --secret-id prod/db",
    "aws ssm get-parameter --name /prod/key --with-decryption",
    "aws iam list-users",
    "cat x > y",
    "echo hi >> notes.txt",
    "cat $(echo file)",
    "echo `whoami`",
    "python3 -c 'print(1)'",
    "bash -c 'ls'",
    "curl https://example.com",
    "sed -i 's/a/b/' file",
    "find . -name '*.pyc' -delete",
    "find . -exec rm {} \;",
    "tar xzf a.tgz",
    "git config user.name x",
    "git branch -D main",
    "psql postgres://ro@host/db -c 'select 1'",   # psql off unless enabled
    "ls; rm -rf /",
    "ls && git push",
    "cat a | tee b",
]


@pytest.mark.parametrize("cmd", ALLOWED)
def test_allowed(cmd):
    v = check_readonly(cmd)
    assert v.allowed, f"{cmd!r} denied: {v.reason}"


@pytest.mark.parametrize("cmd", DENIED)
def test_denied(cmd):
    assert not check_readonly(cmd).allowed, f"{cmd!r} should be denied"


def test_psql_when_enabled():
    assert check_readonly("psql \"$RO_URL\" -c 'select count(*) from deals'", allow_psql=True).allowed
    assert not check_readonly("psql \"$RO_URL\" -c 'delete from deals'", allow_psql=True).allowed
    assert not check_readonly("psql \"$RO_URL\" -f script.sql", allow_psql=True).allowed


def test_fix_guard():
    assert check_fix_command("pytest -q tests").allowed
    assert check_fix_command("npm test && npm run lint").allowed
    assert check_fix_command("git diff --stat && git status").allowed
    assert not check_fix_command("git push origin HEAD").allowed
    assert not check_fix_command("eb deploy Gero-DevLog").allowed
    assert not check_fix_command("sf project deploy start --target-org prod").allowed
    assert not check_fix_command("aws lambda update-function-code --function-name x").allowed
    assert not check_fix_command("rm -rf /").allowed
    assert not check_fix_command("curl -X POST https://hooks.example.com").allowed
