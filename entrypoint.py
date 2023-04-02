#!/usr/bin/env python3
# pylint: disable=E1310

"""
Okteto Sync

Synchronise GitHub deployments with Okteto deployments.
Removes stale GitHub & Okteto deployments.
"""

# Standard lib
from dataclasses import dataclass, field
from typing import Iterator, Union
from operator import attrgetter
from datetime import datetime
import urllib.request
import json as _json
import subprocess
import sys
import os


# Fetch vars from Command line
DRY_RUN = str(sys.argv[1]).lower() in ("1", "true", "on")
GITHUB_TOKEN = sys.argv[2]
OKTETO_DOMAIN = sys.argv[3]
IGNORE_DEPLOYMENTS = sys.argv[4]

print(repr(IGNORE_DEPLOYMENTS))
# sys.exit(1)

IGNORE_DEPLOYMENTS = list(map(str.strip, "Staging, Production".split(",")))


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
        return Response(resp.read(), resp.status, resp.reason)


def get_all_branches() -> list[str]:
    """Return a list of all branches in current repo."""
    req = request_github_api("branches")
    return [data["name"] for data in req.json]


@dataclass
class GitHubDeployment:
    """Methods related to GitHub deployments."""
    deploy_id: int
    name: str
    branch: str
    task: str
    created: Union[datetime, str]
    url: str = field(init=False, default="")
    okteto: "OktetoDeployment" = field(init=False, default=None)

    def __post_init__(self):
        # We need to replace Z with UTC to make fromisoformat work
        created = self.created.replace("Z", "+00:00")
        self.created = datetime.fromisoformat(created)
        self.branch = self.branch.strip("refs/heads/")

    def is_okteto_deployment(self) -> bool:
        """Return True if deployment matches the okteto url."""
        statuses = request_github_api(f"deployments/{self.deploy_id}/statuses")
        for status in statuses.json:
            url = status["environment_url"]
            if OKTETO_DOMAIN in url:
                self.url = url
                return True
        return False

    def delete(self) -> bool:
        """Delete deployment and return True if requests succeeded, else False."""
        ret = request_github_api(f"deployments/{self.deploy_id}", method="DELETE")
        return ret.status == 204

    @classmethod
    def get_okteto_deployments(cls) -> Iterator["GitHubDeployment"]:
        """Return a list of all deployments matching deploy regex."""
        deployments = request_github_api("deployments")
        for deployment in deployments.json:
            obj = cls(
                deployment["id"],
                deployment["environment"],
                deployment["ref"],
                deployment["task"],
                deployment["created_at"],
            )

            # Only yield deployments that are Okteto deployments
            if obj.task == "deploy" and obj.name not in IGNORE_DEPLOYMENTS and obj.is_okteto_deployment():
                yield obj


@dataclass
class OktetoDeployment:
    """An Okteto preview env."""
    name: str
    scope: str
    sleeping: bool
    github: "GitHubDeployment" = field(init=False, default=None)

    def __init__(self, name: str, scope: str, sleeping: str):
        self.name = name
        self.scope = scope
        self.sleeping = sleeping.lower() in ("1", "on", "true")

    def delete(self):
        """Delete the preview environment."""
        subprocess.run(["okteto", "preview", "destroy", self.name], check=True)
        self.sleeping = True

    @classmethod
    def get_all(cls) -> Iterator["OktetoDeployment"]:
        """Return a list of active preview environments."""
        proc = subprocess.run(
            ["okteto", "preview", "list"],
            capture_output=True, check=True, encoding="utf8"
        )
        # We need to scrap the first row as it contains the headers
        if envs := proc.stdout.strip().split("\n")[1:]:
            for env in envs:
                cleaned = filter(None, env.split(" "))
                yield cls(*cleaned)


def connect_deployments(github: list[GitHubDeployment], okteto: list[OktetoDeployment]):
    """Take a list of both GitHub and Okteto deployments and match them to each other."""
    for okteto_deployment in okteto:
        for github_deployment in github:
            if okteto_deployment.name in github_deployment.url:
                okteto_deployment.github = github_deployment
                github_deployment.okteto = okteto_deployment
                break


def run():
    """Main script to sync deployments."""

    # Fetch all required data before processing
    print("# Fetching Branches & Deployments")
    github_branches = get_all_branches()
    print("GitHub Branches:", github_branches)
    github_deployments = list(GitHubDeployment.get_okteto_deployments())
    print("GitHub Deployments:", [env.name for env in github_deployments])
    okteto_deployments = list(OktetoDeployment.get_all())
    print("Okteto Deployments:", [env.name for env in okteto_deployments])
    connect_deployments(github_deployments, okteto_deployments)
    remove_list_github, remove_list_okteto = [], []

    print("")
    print("# Checking Github Environments")
    for deploy in github_deployments:
        if deploy.okteto is None:
            print(f"Okteto deployment missing for: {deploy.name}")
            remove_list_github.append(deploy)

        elif deploy.branch not in github_branches:
            print(f"Branch missing for deployment: {deploy.name}")
            remove_list_github.append(deploy)
            remove_list_okteto.append(deploy.okteto)

    # We need to remove the oldest deployments first, GitHub will only remove the active
    # deployments when all the inactive have been removed. The most recent is always active.
    for deployment in sorted(remove_list_github, key=attrgetter("created")):
        print("Deleting GitHub deployment:", deployment.name, "=>", deployment.deploy_id)
        if not DRY_RUN:
            deployment.delete()

    print("")
    print("# Checking Okteto Environments")
    for deploy in okteto_deployments:
        if deploy.github is None:
            print(f"Github deployment missing for: {deploy.name}")
            remove_list_okteto.append(deploy)

    # Remove any flagged Okteto environments
    for okteto_env in remove_list_okteto:
        print("Deleting Okteto deployment:", okteto_env.name)
        if not DRY_RUN:
            okteto_env.delete()


if __name__ == "__main__":
    run()
