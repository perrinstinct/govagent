"""GitHost port implemented with the GitHub REST API (httpx2).

Opening a fix PR takes five calls, no clone: read the file and its blob sha, read the base
branch head, create the branch, commit the new content (the blob sha makes GitHub reject the
write if the file changed meanwhile), open the pull request.
"""

import base64
from typing import Any, cast
from urllib.parse import quote

import httpx2

from govagent.domain.errors import GitHubError

API_VERSION = "2022-11-28"


class GitHubClient:
    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
        transport: httpx2.BaseTransport | None = None,  # tests inject a MockTransport
        timeout_seconds: float = 30,
    ) -> None:
        self._http = httpx2.Client(
            base_url=api_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "govagent",
            },
            timeout=timeout_seconds,
            transport=transport,
        )

    def get_file(self, repo: str, path: str, ref: str) -> tuple[str, str]:
        data = self._request("GET", f"/repos/{repo}/contents/{quote(path)}", params={"ref": ref})
        file = cast(dict[str, Any], data) if isinstance(data, dict) else {}
        if file.get("type") != "file":
            raise GitHubError(f"{repo}:{path}@{ref} is not a file")
        if file.get("encoding") != "base64":
            raise GitHubError(f"{repo}:{path} is too large for the contents API")
        text = base64.b64decode(str(file["content"])).decode("utf-8")
        return text, str(file["sha"])

    def create_branch(self, repo: str, branch: str, from_ref: str) -> None:
        head = cast(dict[str, Any], self._request("GET", f"/repos/{repo}/git/ref/heads/{from_ref}"))
        sha = cast(dict[str, Any], head["object"])["sha"]
        self._request(
            "POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha}
        )

    def update_file(
        self, repo: str, path: str, branch: str, text: str, sha: str, message: str
    ) -> None:
        content = base64.b64encode(text.encode("utf-8")).decode("ascii")
        self._request(
            "PUT",
            f"/repos/{repo}/contents/{quote(path)}",
            json={"message": message, "content": content, "sha": sha, "branch": branch},
        )

    def open_pr(self, repo: str, head: str, base: str, title: str, body: str) -> str:
        data = self._request(
            "POST",
            f"/repos/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )
        return str(cast(dict[str, Any], data)["html_url"])

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, url, **kwargs)
        except httpx2.HTTPError as exc:
            raise GitHubError(f"GitHub request failed: {method} {url}: {exc}") from exc
        if response.status_code >= 400:
            raise GitHubError(_describe_failure(method, url, response))
        return response.json()


def _describe_failure(method: str, url: str, response: httpx2.Response) -> str:
    try:
        message = str(cast(dict[str, Any], response.json()).get("message", ""))
    except ValueError:
        message = response.text[:200]
    hint = {
        401: "the token is invalid or expired",
        403: "the token lacks a permission (Contents / Pull requests: read and write)",
        404: "not found, or the token has no access to this repository",
        409: "the file changed meanwhile",
        422: "rejected by GitHub (e.g. the branch already exists)",
    }.get(response.status_code, "unexpected error")
    return f"GitHub {method} {url} -> {response.status_code}: {hint} ({message})"
