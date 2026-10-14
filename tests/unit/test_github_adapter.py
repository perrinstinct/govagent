"""GitHubClient against a simulated GitHub API (httpx2.MockTransport): exact calls, no network."""

import base64
import json
from typing import Any

import httpx2
import pytest

from govagent.adapters.github import GitHubClient
from govagent.domain.errors import GitHubError

REPO = "octo/demo"
SPEC = "openapi: 3.0.3\n"


class FakeGitHub:
    def __init__(self, status: dict[str, int] | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        self.status = status or {}

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        key = f"{request.method} {request.url.path}"
        if key in self.status:
            return httpx2.Response(self.status[key], json={"message": "nope"})
        routes: dict[str, Any] = {
            f"GET /repos/{REPO}/contents/api/openapi.yaml": {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(SPEC.encode()).decode() + "\n",
                "sha": "blob-sha",
            },
            f"GET /repos/{REPO}/git/ref/heads/main": {"object": {"sha": "commit-sha"}},
            f"POST /repos/{REPO}/git/refs": {"ref": "refs/heads/govagent/x"},
            f"PUT /repos/{REPO}/contents/api/openapi.yaml": {"content": {}},
            f"POST /repos/{REPO}/pulls": {"html_url": "https://github.com/octo/demo/pull/7"},
        }
        return httpx2.Response(200, json=routes[key])

    def body(self, index: int) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def client(github: FakeGitHub) -> GitHubClient:
    return GitHubClient("token-123", transport=httpx2.MockTransport(github))


def test_get_file_decodes_content_and_returns_the_blob_sha() -> None:
    github = FakeGitHub()

    text, sha = client(github).get_file(REPO, "api/openapi.yaml", "main")

    assert (text, sha) == (SPEC, "blob-sha")
    request = github.requests[0]
    assert request.url.params["ref"] == "main"
    assert request.headers["Authorization"] == "Bearer token-123"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_create_branch_starts_from_the_base_head_commit() -> None:
    github = FakeGitHub()

    client(github).create_branch(REPO, "govagent/abc", "main")

    assert github.body(1) == {"ref": "refs/heads/govagent/abc", "sha": "commit-sha"}


def test_update_file_sends_base64_content_and_the_replaced_blob_sha() -> None:
    github = FakeGitHub()

    client(github).update_file(REPO, "api/openapi.yaml", "govagent/abc", "new\n", "blob-sha", "Fix")

    body = github.body(0)
    assert base64.b64decode(body["content"]) == b"new\n"
    assert (body["sha"], body["branch"], body["message"]) == ("blob-sha", "govagent/abc", "Fix")


def test_open_pr_returns_its_url() -> None:
    github = FakeGitHub()

    url = client(github).open_pr(REPO, "govagent/abc", "main", "Title", "Body")

    assert url == "https://github.com/octo/demo/pull/7"
    assert github.body(0) == {
        "title": "Title",
        "head": "govagent/abc",
        "base": "main",
        "body": "Body",
    }


@pytest.mark.parametrize(
    ("status", "hint"),
    [
        (401, "invalid or expired"),
        (403, "lacks a permission"),
        (404, "no access"),
        (422, "already exists"),
    ],
)
def test_errors_explain_the_likely_cause(status: int, hint: str) -> None:
    github = FakeGitHub(status={f"POST /repos/{REPO}/git/refs": status})

    with pytest.raises(GitHubError, match=hint):
        client(github).create_branch(REPO, "govagent/abc", "main")


def test_directory_is_not_a_file() -> None:
    def directory(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=[{"name": "a.yaml"}])

    gh = GitHubClient("t", transport=httpx2.MockTransport(directory))

    with pytest.raises(GitHubError, match="is not a file"):
        gh.get_file(REPO, "api", "main")
