#!/usr/bin/env python3
# pylint: disable=E1310 R0903

"""
Okteto Sync

Synchronise GitHub deployments with Okteto deployments.
Removes stale GitHub & Okteto deployments.
"""

# Standard lib
from dataclasses import dataclass, field
from typing import Iterator, Union, Any
from urllib import parse as urlparse
from operator import attrgetter
from datetime import datetime
import urllib.request
import json as _json
import subprocess
import sys
import os
import re


# Fetch vars from Command line
DRY_RUN = str(sys.argv[1]).lower() in ("yes", "true", "y", "1", "on")
GITHUB_TOKEN = sys.argv[2]
OKTETO_DOMAIN = sys.argv[3]
IGNORE_DEPLOYMENTS = list(filter(None, map(str.strip, sys.argv[4].replace("\n", ",").split(","))))

# Fetch vars from default environment variables
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
PER_PAGE = 100


class TC:
    """Ascii color codes."""
    GREEN = "\033[92m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    RESET = "\033[0m"


class Response:
    """Basic urllib response object."""
    links_regex = re.compile(r'<([^>]+)>.*?rel="([\w\s]+)".*')

    def __init__(self, raw_resp):
        self.raw_data: bytes = raw_resp.read()
        self.status: int = raw_resp.status
        self.reason: str = raw_resp.reason
        self.headers: dict[str: str] = raw_resp.headers

    def json(self):
        """Returns the response as a json object."""
        try:
            return _json.loads(self.raw_data)
        except _json.JSONDecodeError:
            return None

    @property
    def links(self) -> dict[str, dict[str, str]]:
        """Parse the link header and return as structured data."""
        if "link" not in self.headers:
            return {}

        links = {}
        # Use regex to parse the rel links
        for match in self.links_regex.finditer(self.headers["link"]):
            link = match.group(1)
            rel = match.group(2)

            # Extract url params for easier access
            query_params = dict(urlparse.parse_qsl(urlparse.urlsplit(link).query))

            # Construct standardized link structure
            for true_rel in rel.split(" "):
                links[true_rel] = {"url": link, "rel": true_rel, **query_params}

        return links


def request_github_api(endpoint: str, params: dict = None, method="GET") -> Response:
    """Make web request to GitHub API."""
    query = urlparse.urlencode(params or {})
    req = urllib.request.Request(
        url=f"{GITHUB_API_URL}/repos/{REPOSITORY}/{endpoint}?{query}",
        method=method,
        headers={
            "X-GitHub-Api-Version": "2022-11-28",
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return Response(resp)


def get_paged_resp(url: str, params: dict[str, Any] = None) -> Iterator[dict]:
    """Return an iterator of paged results, looping until all resources are collected."""
    params = params or {}
    params.update(page="1")
    params.setdefault("per_page", min(PER_PAGE, 100))

    while True:
        resp = request_github_api(url, params=params)
        yield from resp.json()

        # Continue with next page if one is found
        if "next" in resp.links:
            page = resp.links["next"]["page"]
            params["page"] = page
        else:
            break


def get_all_branches() -> list[str]:
    """Return a list of all branches in current repo."""
    return [data["name"] for data in get_paged_resp("branches")]


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
        # We only need to check the first page of results. Anymore and things will really start slowing down
        statuses = request_github_api(f"deployments/{self.deploy_id}/statuses")
        for status in statuses.json():
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
        for deployment in get_paged_resp("deployments"):
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

    def __init__(self, name: str, scope: str, sleeping: str, **_):
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

        headings = None
        for row in proc.stdout.strip().split("\n"):
            # The data only starts after the heading
            # We also use the hading to create structured data
            if headings is None and "Name" in row and "Scope" in row:
                headings = list(map(str.lower, filter(None, row.split(" "))))

            elif headings:
                # Combine row with headers to create structured data
                cleaned = filter(None, row.split(" "))
                structured = dict(zip(headings, cleaned))
                yield cls(**structured)


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
    print(TC.GREEN + "Fetching Branches & Deployments", TC.RESET)
    github_branches = get_all_branches()
    print(TC.CYAN + "GitHub Branches:", TC.RESET, github_branches)
    github_deployments = list(GitHubDeployment.get_okteto_deployments())
    print(TC.CYAN + "GitHub Deployments:", TC.RESET, [env.name for env in github_deployments])
    okteto_deployments = list(OktetoDeployment.get_all())
    print(TC.CYAN + "Okteto Deployments:", TC.RESET, [env.name for env in okteto_deployments])
    connect_deployments(github_deployments, okteto_deployments)
    remove_list_github, remove_list_okteto = [], []

    print("")
    print(TC.GREEN + "Checking Github Environments", TC.RESET)
    for deploy in github_deployments:
        if deploy.okteto is None:
            print(TC.CYAN + "Okteto deployment missing for:", TC.RESET, deploy.name)
            remove_list_github.append(deploy)

        elif deploy.branch not in github_branches:
            print(TC.CYAN + "Branch missing for deployment:", TC.RESET, deploy.name)
            remove_list_github.append(deploy)
            remove_list_okteto.append(deploy.okteto)

    # We need to remove the oldest deployments first, GitHub will only remove the active
    # deployments when all the inactive have been removed. The most recent is always active.
    for deployment in sorted(remove_list_github, key=attrgetter("created")):
        print(TC.YELLOW + "Deleting:", TC.RESET, deployment.name, "=>", deployment.deploy_id)
        if not DRY_RUN:
            deployment.delete()

    print("")
    print(TC.GREEN + "Checking Okteto Environments", TC.RESET)
    for deploy in okteto_deployments:
        if deploy.github is None:
            print(TC.CYAN + "Github deployment missing for:", TC.RESET, deploy.name)
            remove_list_okteto.append(deploy)

    # Remove any flagged Okteto environments
    for okteto_env in remove_list_okteto:
        print(TC.YELLOW + "Deleting:", TC.RESET, okteto_env.name)
        if not DRY_RUN:
            okteto_env.delete()


if __name__ == "__main__":
    run()
