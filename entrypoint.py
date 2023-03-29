#!/usr/bin/env python3

"""
Okteto Sync

Synchronise GitHub deployments with Okteto deployments.
Removes stale GitHub & Okteto deployments.
"""

# Standard lib
from dataclasses import dataclass, field
from operator import attrgetter
from datetime import datetime
from typing import Iterator
import urllib.request
import json as _json
import subprocess
import sys
import os
import re


# Fetch vars from Command line
DRY_RUN = str(sys.argv[1]).lower() in ("1", "true", "on")
GITHUB_TOKEN = sys.argv[2]
GITHUB_DEPLOY_REGEX = sys.argv[3]
OKTETO_DEPLOY_REGEX = sys.argv[4]

# Fetch vars from default environment variables
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
REPOSITORY = os.environ["GITHUB_REPOSITORY"]


@dataclass
class Response:
    """Basic urllib response object."""
    raw_data: bytes
    status: int
    reason: str

    @property
    def json(self):
        """Returns the response as a json object."""
        try:
            return _json.loads(self.raw_data)
        except _json.JSONDecodeError:
            return None


def request_github_api(endpoint: str, method="GET") -> Response:
    """Make web request to GitHub API."""
    req = urllib.request.Request(
        url=f"{GITHUB_API_URL}/repos/{REPOSITORY}/{endpoint}",
        method=method,
        headers={
            "X-GitHub-Api-Version": "2022-11-28",
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
        }
    )
    with urllib.request.urlopen(req) as resp:
        return Response(resp.read, resp.status, resp.reason)


def get_all_branches() -> list[str]:
    """Return a list of all branches in current repo."""
    req = request_github_api("branches")
    return [data["name"] for data in req.json]


class GitHubDeployments:
    """Methods related to GitHub deployments."""

    def __init__(self, data):
        self.name: str = data["environment"]
        self.branch: str = data["ref"]
        self.deploy_id: int = data["id"]

        # We need to replace Z with UTC to make fromisoformat work
        created = data["created_at"].replace("Z", "+00:00")
        self.created = datetime.fromisoformat(created)

    def __repr__(self):
        return f"{self.__class__.__name__}(name='{self.name}', created='{self.created}')"

    def is_okteto_deploy(self) -> bool:
        """Return True if environment matching Okteto regex."""
        return bool(re.match(GITHUB_DEPLOY_REGEX, self.name))

    def delete(self) -> bool:
        """Delete deployment and return True if requests succeeded, else False."""
        ret = request_github_api(f"deployments/{self.deploy_id}", method="DELETE")
        return ret.status == 204

    @classmethod
    def get_all(cls) -> Iterator["GitHubDeployments"]:
        """Return a list of all deployments matching deploy regex."""
        data = request_github_api("deployments")
        for deployment in data.json:
            obj = cls(deployment)
            if obj.is_okteto_deploy():
                yield obj


@dataclass
class OktetoEnv:
    """An Okteto preview env."""
    name: str
    scope: str
    sleeping: bool
    branch: str = field(init=False)

    def __post_init__(self):
        if match := re.match(OKTETO_DEPLOY_REGEX, self.name):
            self.branch = match[1]
        else:
            self.branch = ""

    def delete(self):
        """Delete the preview environment."""
        subprocess.run(["okteto", "preview", "destroy", self.name], check=True)
        self.sleeping = True

    @classmethod
    def get_all(cls) -> Iterator["OktetoEnv"]:
        """Return a list of active preview environments."""
        proc = subprocess.run(
            ["okteto", "preview", "list"],
            capture_output=True, check=True, encoding="utf8"
        )
        if envs := proc.stdout.strip().split("\n")[1:]:
            for env in envs:
                cleaned = filter(None, env.split(" "))
                obj = cls(*cleaned)
                if obj.branch:
                    yield obj
                else:
                    print(f"Unable to determine branch name for {obj.name}: ignoring")


def run():
    """Main script to sync deployments."""

    # Fetch all required data before processing
    print("# Fetching list of branches...")
    github_branches = get_all_branches()
    print("# Fetching list of GitHub deployments...")
    all_deployments = list(GitHubDeployments.get_all())
    print("# Fetching list of Okteto deployments...")
    all_okteto_envs = list(OktetoEnv.get_all())
    okteto_branches = [env.branch for env in all_okteto_envs if env.branch]

    print("")
    print("# Detected Branches & Deployments")
    print("GitHub Branches:", github_branches)
    print("GitHub Deployments:", [f"{env.name}:{env.branch}" for env in all_deployments])
    print("Okteto Deployments:", [f"{env.name}:{env.branch}" for env in all_okteto_envs])

    print("")
    remove_list_github = []
    print("# Checking Github Environments")
    # Delete GitHub deployments where the related Okteto deployment has been removed
    # Or the branch that relates to it, no longer exists
    for deploy in all_deployments:
        if deploy.branch not in github_branches:
            print(f"Branch missing for environment: {deploy.name}")
            remove_list_github.append(deploy)

        elif deploy.branch not in okteto_branches:
            print(f"Okteto environment is missing for: {deploy.name}")
            remove_list_github.append(deploy)

    print("")
    remove_list_okteto = []
    print("# Checking Okteto Environments")
    # Delete Okteto deployments where the related GitHub branch has been remove
    for okteto_env in all_okteto_envs:
        if okteto_env.branch not in github_branches:
            # Delete Okteto Deployment
            print(f"Branch '{okteto_env.branch}' missing for Okteto deployment: {okteto_env.name}")
            remove_list_okteto.append(okteto_env)

    if not DRY_RUN:
        # We need to remove the oldest deployments first, Github will only remove the active
        # deployments when all the inactive have been removed. The most recent is always active.
        for deployment in sorted(remove_list_github, key=attrgetter("created")):
            print("Deleting GitHub deployment:", deployment.name, "=>", deployment.deploy_id)
            deployment.delete()

        # Remove any flagged Okteto environments
        for okteto_env in remove_list_okteto:
            print("Deleting Okteto deployment:", okteto_env.name)
            okteto_env.delete()


if __name__ == "__main__":
    run()
