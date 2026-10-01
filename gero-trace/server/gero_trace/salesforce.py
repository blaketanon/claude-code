"""Read-only Salesforce access for the investigator through the `sf` CLI.

Auth is the JWT bearer flow: a Connected App with a certificate and an integration user whose profile
only has read access (plus View Setup and Configuration, View All Data as needed for history/logs).
"""
import logging
import os
import shutil
import subprocess
import tempfile

log = logging.getLogger(__name__)

PACKAGE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
    <types><members>*</members><name>ApexClass</name></types>
    <types><members>*</members><name>ApexTrigger</name></types>
    <types><members>*</members><name>Flow</name></types>
    <types><members>*</members><name>CustomObject</name></types>
    <types><members>*</members><name>ValidationRule</name></types>
    <types><members>*</members><name>WorkflowRule</name></types>
    <types><members>*</members><name>WorkflowFieldUpdate</name></types>
    <types><members>*</members><name>ApprovalProcess</name></types>
    <types><members>*</members><name>AssignmentRules</name></types>
    <types><members>*</members><name>CustomMetadata</name></types>
    <types><members>*</members><name>CustomLabels</name></types>
    <types><members>*</members><name>NamedCredential</name></types>
    <types><members>*</members><name>RemoteSiteSetting</name></types>
    <types><members>*</members><name>LightningComponentBundle</name></types>
    <types><members>*</members><name>AuraDefinitionBundle</name></types>
    <types><members>*</members><name>ApexPage</name></types>
    <types><members>*</members><name>PlatformEventChannel</name></types>
    <types><members>*</members><name>Layout</name></types>
    <types><members>*</members><name>QuickAction</name></types>
    <types><members>*</members><name>GlobalValueSet</name></types>
    <version>62.0</version>
</Package>
"""


class SalesforceAccess:
    def __init__(self, username, client_id, jwt_key, login_url, alias="trace"):
        self.username = username
        self.client_id = client_id
        self.jwt_key = jwt_key
        self.login_url = login_url or "https://login.salesforce.com"
        self.alias = alias
        self._logged_in = False

    @classmethod
    def from_config(cls, cfg):
        return cls(cfg.get("SF_USERNAME"), cfg.get("SF_CLIENT_ID"), cfg.get("SF_JWT_KEY"), cfg.get("SF_LOGIN_URL"), cfg.get("SF_ORG_ALIAS", "trace"))

    @property
    def configured(self):
        return bool(self.username and self.client_id and self.jwt_key and shutil.which("sf"))

    def login(self):
        if self._logged_in or not self.configured:
            return self._logged_in
        fd, key_path = tempfile.mkstemp(suffix=".key")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(self.jwt_key if self.jwt_key.endswith("\n") else self.jwt_key + "\n")
            os.chmod(key_path, 0o600)
            r = subprocess.run(["sf", "org", "login", "jwt", "--client-id", self.client_id, "--jwt-key-file", key_path,
                                "--username", self.username, "--instance-url", self.login_url, "--alias", self.alias, "--json"],
                               capture_output=True, text=True, timeout=120)
        finally:
            os.unlink(key_path)
        if r.returncode != 0:
            raise RuntimeError(f"sf org login jwt failed: {r.stderr.strip()[-500:] or r.stdout.strip()[-500:]}")
        self._logged_in = True
        log.info("salesforce: logged in as %s (alias %s)", self.username, self.alias)
        return True

    def retrieve_metadata(self, dest):
        """Pull the org's declarative + Apex metadata into <dest>/force-app so Claude can grep it."""
        self.login()
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "sfdx-project.json"), "w") as f:
            f.write('{"packageDirectories":[{"path":"force-app","default":true}],"namespace":"","sourceApiVersion":"62.0"}\n')
        os.makedirs(os.path.join(dest, "force-app"), exist_ok=True)
        with open(os.path.join(dest, "package.xml"), "w") as f:
            f.write(PACKAGE_XML)
        r = subprocess.run(["sf", "project", "retrieve", "start", "--manifest", "package.xml", "--target-org", self.alias,
                            "--ignore-conflicts", "--wait", "30", "--json"], cwd=dest, capture_output=True, text=True, timeout=2400)
        if r.returncode != 0:
            raise RuntimeError(f"metadata retrieve failed: {r.stderr.strip()[-500:] or r.stdout.strip()[-800:]}")
        with open(os.path.join(dest, "README.md"), "w") as f:
            f.write("# Salesforce metadata (read-only snapshot)\n\nRetrieved with `sf project retrieve start`. "
                    "Apex is under force-app/main/default/classes and triggers, Flows under flows, objects/fields/validation "
                    "rules under objects. Do not edit; the next sync overwrites it.\n")
        log.info("salesforce: metadata retrieved into %s", dest)
